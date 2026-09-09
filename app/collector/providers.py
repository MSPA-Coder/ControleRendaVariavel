from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.collector.rtd import Instrument, QuoteValue


class ManagedQuoteProvider(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def fetch(self, instruments: list[Instrument]) -> list[QuoteValue]: ...


class CollectorProviderManager:
    """Keeps one provider open across cycles and closes it on demand.

    O laço fecha o provedor nos trechos ociosos (Profit fechado, fora da
    agenda) e volta a pedir no próximo ciclo; este gerenciador reabre sozinho
    quando isso acontece, sem o chamador precisar saber se ainda está aberto.
    """

    def __init__(self, provider_factory: Callable[[], ManagedQuoteProvider]) -> None:
        self.provider_factory = provider_factory
        self.provider: ManagedQuoteProvider | None = None

    def get(self) -> ManagedQuoteProvider:
        if self.provider is None:
            provider = self.provider_factory()
            provider.open()
            self.provider = provider
        return self.provider

    def close(self) -> None:
        provider = self.provider
        self.provider = None
        if provider is not None:
            provider.close()
