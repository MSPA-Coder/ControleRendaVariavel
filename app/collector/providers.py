from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.collector.rtd import Instrument, QuoteValue
from app.models import CollectorMode


class ManagedQuoteProvider(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def fetch(self, instruments: list[Instrument]) -> list[QuoteValue]: ...


class CollectorProviderManager:
    """Keeps one provider open and swaps it when the configured mode changes."""

    def __init__(
        self,
        provider_factory: Callable[[CollectorMode], ManagedQuoteProvider],
    ) -> None:
        self.provider_factory = provider_factory
        self.mode: CollectorMode | None = None
        self.provider: ManagedQuoteProvider | None = None

    def get(self, mode: CollectorMode) -> ManagedQuoteProvider:
        if self.provider is not None and self.mode == mode:
            return self.provider
        self.close()
        provider = self.provider_factory(mode)
        provider.open()
        self.provider = provider
        self.mode = mode
        return provider

    def close(self) -> None:
        provider = self.provider
        self.provider = None
        self.mode = None
        if provider is not None:
            provider.close()
