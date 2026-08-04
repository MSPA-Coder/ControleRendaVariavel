from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.collector import CollectorProviderManager
from app.models import CollectorMode
from app.rtd import Instrument, QuoteValue

pytestmark = [pytest.mark.architecture]


class FakeManagedProvider:
    def __init__(self) -> None:
        self.open_count = 0
        self.close_count = 0

    def open(self) -> None:
        self.open_count += 1

    def close(self) -> None:
        self.close_count += 1

    def fetch(self, instruments: list[Instrument]) -> list[QuoteValue]:
        return [
            QuoteValue(
                instrument.position_id,
                Decimal("1"),
                Decimal("1"),
                "A",
                datetime.now(UTC),
            )
            for instrument in instruments
        ]


def test_provider_manager_reuses_and_swaps_collectors() -> None:
    excel = FakeManagedProvider()
    direct = FakeManagedProvider()
    providers = {
        CollectorMode.EXCEL: excel,
        CollectorMode.DIRECT: direct,
    }
    manager = CollectorProviderManager(lambda mode: providers[mode])

    assert manager.get(CollectorMode.EXCEL) is excel
    assert manager.get(CollectorMode.EXCEL) is excel
    assert excel.open_count == 1

    assert manager.get(CollectorMode.DIRECT) is direct
    assert excel.close_count == 1
    assert direct.open_count == 1

    manager.close()
    assert direct.close_count == 1
