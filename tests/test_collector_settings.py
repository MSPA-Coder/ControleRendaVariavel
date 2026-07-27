import pytest

from app.collector_settings import parse_collector_settings
from app.models import CollectorMode


def test_parse_collector_settings_accepts_both_modes() -> None:
    excel = parse_collector_settings(
        {"collector_mode": "excel", "poll_interval_seconds": "2"}
    )
    direct = parse_collector_settings(
        {"collector_mode": "direct", "poll_interval_seconds": "15"}
    )

    assert (excel.collector_mode, excel.poll_interval_seconds) == (CollectorMode.EXCEL, 2)
    assert (direct.collector_mode, direct.poll_interval_seconds) == (
        CollectorMode.DIRECT,
        15,
    )


@pytest.mark.parametrize(
    "form",
    [
        {"collector_mode": "invalid", "poll_interval_seconds": "2"},
        {"collector_mode": "excel", "poll_interval_seconds": "0"},
        {"collector_mode": "direct", "poll_interval_seconds": "3601"},
        {"collector_mode": "direct", "poll_interval_seconds": "1.5"},
    ],
)
def test_parse_collector_settings_rejects_invalid_values(form: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        parse_collector_settings(form)
