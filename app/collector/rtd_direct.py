from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from app.collector.rtd import Instrument, QuoteValue, parse_decimal

RTD_TYPELIB_ID = "{EFCFBDCA-78A5-450B-8228-346C4F44D5B8}"
QUOTE_FIELDS = ("ULT", "FEC", "EST")


def quote_fields(instrument: Instrument) -> tuple[str, ...]:
    return (
        (*QUOTE_FIELDS, instrument.book_field)
        if instrument.book_field is not None
        else QUOTE_FIELDS
    )


class RtdUpdateEvent:
    """Callback object used by an RTD server to signal pending updates."""

    _typelib_guid_ = RTD_TYPELIB_ID
    _typelib_version_ = (1, 0)
    _com_interfaces_ = ["IRTDUpdateEvent"]

    def __init__(self, heartbeat_interval_ms: int = 15_000) -> None:
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.update_pending = False
        self.update_count = 0
        self.disconnected = False

    def UpdateNotify(self) -> None:
        self.update_pending = True
        self.update_count += 1

    def HeartbeatInterval(self) -> int:
        return self.heartbeat_interval_ms

    def SetHeartbeatInterval(self, value: int) -> None:
        self.heartbeat_interval_ms = int(value)

    def Disconnect(self) -> None:
        self.disconnected = True


def decode_refresh_data(value: object) -> dict[int, object]:
    """Normalizes the 2 x N SAFEARRAY returned by IRTDServer.RefreshData."""

    payload = value
    if (
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes)
        and len(value) == 2
        and isinstance(value[1], int)
    ):
        payload = value[0]
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        return {}
    if len(payload) != 2:
        return {}
    topic_ids, topic_values = payload
    if (
        not isinstance(topic_ids, Sequence)
        or isinstance(topic_ids, str | bytes)
        or not isinstance(topic_values, Sequence)
        or isinstance(topic_values, str | bytes)
    ):
        return {}
    return {
        int(topic_id): topic_value
        for topic_id, topic_value in zip(topic_ids, topic_values, strict=False)
    }


class DirectRtdQuoteProvider:
    """Experimental IRTDServer client that does not instantiate Excel."""

    def __init__(
        self,
        *,
        prog_id: str,
        timeout_seconds: float = 10,
        refresh_seconds: float = 0.1,
        server_factory: Callable[[str], Any] | None = None,
        callback_wrapper: Callable[[RtdUpdateEvent], object] | None = None,
        pump_messages: Callable[[], None] | None = None,
        com_initialize: Callable[[], None] | None = None,
        com_uninitialize: Callable[[], None] | None = None,
    ) -> None:
        self.prog_id = prog_id
        self.timeout_seconds = timeout_seconds
        self.refresh_seconds = refresh_seconds
        self._server_factory = server_factory
        self._callback_wrapper = callback_wrapper
        self._pump_messages = pump_messages
        self._com_initialize = com_initialize
        self._com_uninitialize = com_uninitialize
        self._com_initialized = False
        self._server: Any | None = None
        self._callback: RtdUpdateEvent | None = None
        self._callback_keepalive: object | None = None
        self._topic_ids: list[int] = []
        self._next_topic_id = 1
        self._topics: dict[int, tuple[Instrument, str]] = {}
        self._values: dict[int, object] = {}
        self._initial_result_types: list[str] = []
        self._refresh_result_types: list[str] = []

    def __enter__(self) -> DirectRtdQuoteProvider:
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _load_com_runtime(self) -> None:
        if self._server_factory is not None:
            return
        try:
            import comtypes
            import comtypes.client
            from comtypes import COMObject
        except ImportError as exc:
            raise RuntimeError(
                "instale o extra 'rtd' e execute este comando no Windows"
            ) from exc

        rtd_types = comtypes.client.GetModule((RTD_TYPELIB_ID, 1, 0))

        class ComtypesRtdUpdateEvent(COMObject):  # type: ignore[misc]
            _com_interfaces_ = [rtd_types.IRTDUpdateEvent]

            def __init__(self, state: RtdUpdateEvent) -> None:
                super().__init__()
                self.state = state

            def UpdateNotify(self) -> None:
                self.state.UpdateNotify()

            def _get_HeartbeatInterval(self) -> int:
                return self.state.HeartbeatInterval()

            def _set_HeartbeatInterval(self, value: int) -> None:
                self.state.SetHeartbeatInterval(value)

            def Disconnect(self) -> None:
                self.state.Disconnect()

        class ComtypesServerAdapter:
            def __init__(self, server: Any) -> None:
                self.server = server

            def ServerStart(self, callback: object) -> object:
                return self.server.ServerStart(callback)

            def ConnectData(
                self,
                topic_id: int,
                strings: tuple[str, str],
                _get_new_values: bool,
            ) -> object:
                result = self.server.ConnectData(topic_id, strings)
                if (
                    isinstance(result, Sequence)
                    and not isinstance(result, str | bytes)
                    and len(result) == 2
                ):
                    return result[1]
                return result

            def RefreshData(self, _topic_count: int) -> object:
                result = self.server.RefreshData()
                if (
                    isinstance(result, Sequence)
                    and not isinstance(result, str | bytes)
                    and len(result) == 2
                ):
                    topic_count, payload = result
                    return payload, topic_count
                return result

            def DisconnectData(self, topic_id: int) -> None:
                self.server.DisconnectData(topic_id)

            def ServerTerminate(self) -> None:
                self.server.ServerTerminate()

        def pump_messages() -> None:
            comtypes.client.PumpEvents(0)

        self._server_factory = lambda prog_id: ComtypesServerAdapter(
            comtypes.client.CreateObject(prog_id, interface=rtd_types.IRtdServer)
        )
        self._pump_messages = pump_messages
        self._com_initialize = comtypes.CoInitialize
        self._com_uninitialize = comtypes.CoUninitialize

        def callback_wrapper(callback: RtdUpdateEvent) -> object:
            com_callback = ComtypesRtdUpdateEvent(callback)
            self._callback_keepalive = com_callback
            return com_callback.QueryInterface(rtd_types.IRTDUpdateEvent)

        self._callback_wrapper = callback_wrapper

    def open(self) -> None:
        if self._server is not None:
            return
        self._load_com_runtime()
        if self._server_factory is None or self._callback_wrapper is None:
            raise RuntimeError("runtime COM direto não configurado")
        if self._com_initialize is not None:
            self._com_initialize()
            self._com_initialized = True
        try:
            callback = RtdUpdateEvent()
            server = self._server_factory(self.prog_id)
            started = int(server.ServerStart(self._callback_wrapper(callback)))
            if started <= 0:
                raise RuntimeError("servidor RTD recusou ServerStart")
            self._callback = callback
            self._server = server
        except Exception:
            if self._com_initialized and self._com_uninitialize is not None:
                self._com_uninitialize()
                self._com_initialized = False
            raise

    def close(self) -> None:
        server = self._server
        callback_keepalive = self._callback_keepalive
        self._server = None
        self._callback = None
        topic_ids, self._topic_ids = self._topic_ids, []
        self._next_topic_id = 1
        self._topics = {}
        self._values = {}
        try:
            if server is not None:
                for topic_id in topic_ids:
                    with suppress(Exception):
                        server.DisconnectData(topic_id)
                server.ServerTerminate()
        finally:
            self._callback_keepalive = None
            del callback_keepalive
            if self._com_initialized and self._com_uninitialize is not None:
                self._com_uninitialize()
                self._com_initialized = False

    def _subscribe_snapshot(self, instruments: list[Instrument]) -> None:
        """Reconnects every topic so each fetch starts from a fresh RTD snapshot."""

        if self._server is None:
            raise RuntimeError("sessão RTD direta não inicializada")
        if self._pump_messages is not None:
            self._pump_messages()
        if self._callback is None:
            raise RuntimeError("callback RTD direto não inicializado")
        self._callback.update_pending = False
        for topic_id in self._topic_ids:
            self._server.DisconnectData(topic_id)
        self._topic_ids = []
        self._topics = {}
        self._values = {}
        self._initial_result_types = []
        self._refresh_result_types = []

        for instrument in instruments:
            for field in quote_fields(instrument):
                topic_id = self._next_topic_id
                self._next_topic_id += 1
                self._topics[topic_id] = (instrument, field)
                initial = self._server.ConnectData(
                    topic_id,
                    (instrument.topic, field),
                    True,
                )
                if initial is not None:
                    self._values[topic_id] = initial
                self._initial_result_types.append(type(initial).__name__)
                self._topic_ids.append(topic_id)

    def fetch(self, instruments: list[Instrument]) -> list[QuoteValue]:
        if not instruments:
            return []
        self.close()
        self.open()
        try:
            return self._fetch_connected(instruments)
        finally:
            self.close()

    def _fetch_connected(self, instruments: list[Instrument]) -> list[QuoteValue]:
        if self._server is None or self._callback is None:
            raise RuntimeError("sessão RTD direta não inicializada")

        self._subscribe_snapshot(instruments)

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if self._pump_messages is not None:
                self._pump_messages()
            if self._callback.disconnected:
                raise ConnectionError("servidor RTD encerrou a conexão")
            if self._callback.update_pending or len(self._values) < len(self._topics):
                self._callback.update_pending = False
                refreshed = self._server.RefreshData(0)
                self._refresh_result_types.append(type(refreshed).__name__)
                decoded = decode_refresh_data(refreshed)
                self._values.update(decoded)
            try:
                return self._build_quotes(instruments, self._topics, self._values)
            except ValueError:
                time.sleep(self.refresh_seconds)
        raise TimeoutError(
            f"RTD direto não respondeu em {self.timeout_seconds:g}s "
            f"(callbacks={self._callback.update_count}, "
            f"tipos iniciais={','.join(self._initial_result_types)}, "
            f"tipos refresh={','.join(self._refresh_result_types[-3:])})"
        )

    @staticmethod
    def _build_quotes(
        instruments: list[Instrument],
        topics: dict[int, tuple[Instrument, str]],
        values: dict[int, object],
    ) -> list[QuoteValue]:
        by_position: dict[int, dict[str, object]] = {
            instrument.position_id: {} for instrument in instruments
        }
        for topic_id, value in values.items():
            topic = topics.get(topic_id)
            if topic is not None:
                instrument, field = topic
                by_position[instrument.position_id][field] = value

        observed_at = datetime.now(UTC)
        quotes: list[QuoteValue] = []
        for instrument in instruments:
            fields = by_position[instrument.position_id]
            required_fields = quote_fields(instrument)
            if not all(field in fields for field in required_fields):
                raise ValueError("campos RTD ainda incompletos")
            instrument_status = str(fields["EST"] or "")[:16]
            last_trade_price = parse_decimal(fields["ULT"])
            price_field = instrument.effective_price_field(instrument_status)
            quotes.append(
                QuoteValue(
                    position_id=instrument.position_id,
                    last_price=(
                        parse_decimal(fields[price_field])
                        if price_field != "ULT"
                        else last_trade_price
                    ),
                    previous_close=parse_decimal(fields["FEC"]),
                    instrument_status=instrument_status,
                    observed_at=observed_at,
                    last_trade_price=last_trade_price,
                )
            )
        return quotes
