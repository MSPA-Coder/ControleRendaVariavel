from __future__ import annotations

from decimal import Decimal

import pytest

from app.rtd import Instrument
from app.rtd_direct import DirectRtdQuoteProvider, RtdUpdateEvent, decode_refresh_data

pytestmark = [pytest.mark.business_rule]


class FakeDirectServer:
    def __init__(self) -> None:
        self.callback: RtdUpdateEvent | None = None
        self.disconnected: list[int] = []
        self.terminated = False
        self.snapshot = 0
        self.values: dict[int, object] = {}

    def ServerStart(self, callback: RtdUpdateEvent) -> int:
        self.callback = callback
        return 1

    def ConnectData(
        self,
        topic_id: int,
        strings: tuple[str, str],
        _get_new_values: bool,
    ) -> object:
        field = strings[1]
        if field == "ULT":
            self.snapshot += 1
            assert self.callback is not None
            self.callback.UpdateNotify()
        value = {
            "ULT": f"{11 + self.snapshot},34",
            "FEC": f"{11 + self.snapshot},00",
            "EST": "A",
        }[field]
        self.values[topic_id] = value
        return value

    def RefreshData(self, _topic_count: int) -> tuple[tuple[object, ...], tuple[object, ...]]:
        return tuple(self.values), tuple(self.values.values())

    def DisconnectData(self, topic_id: int) -> None:
        self.disconnected.append(topic_id)
        self.values.pop(topic_id, None)

    def ServerTerminate(self) -> None:
        self.terminated = True


def test_decode_refresh_data_accepts_safearray_and_out_count() -> None:
    payload = ((1, 2), ("10,00", "11,00"))

    assert decode_refresh_data(payload) == {1: "10,00", 2: "11,00"}
    assert decode_refresh_data((payload, 2)) == {1: "10,00", 2: "11,00"}
    assert decode_refresh_data([[1, 2], ["10,00", "11,00"]]) == {
        1: "10,00",
        2: "11,00",
    }


def test_direct_provider_reads_topics_and_closes_server() -> None:
    server = FakeDirectServer()

    def server_factory(_prog_id: str) -> FakeDirectServer:
        return server

    provider = DirectRtdQuoteProvider(
        prog_id="server",
        server_factory=server_factory,
        callback_wrapper=lambda callback: callback,
    )
    with provider:
        values = provider.fetch([Instrument(7, "TEST3", "B")])
        repeated = provider.fetch([Instrument(7, "TEST3", "B")])

    assert values[0].position_id == 7
    assert repeated[0].last_price == Decimal("13.34")
    assert values[0].last_price == Decimal("12.34")
    assert values[0].previous_close == Decimal("12.00")
    assert values[0].instrument_status == "A"
    assert server.disconnected == [1, 2, 3, 1, 2, 3]
    assert server.terminated is True


def test_callback_tracks_update_and_disconnect() -> None:
    callback = RtdUpdateEvent()

    callback.UpdateNotify()
    callback.SetHeartbeatInterval(2_500)
    callback.Disconnect()

    assert callback.update_pending is True
    assert callback.HeartbeatInterval() == 2_500
    assert callback.disconnected is True
