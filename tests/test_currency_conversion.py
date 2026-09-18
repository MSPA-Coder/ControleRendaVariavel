from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.core.currency import TaxaDeCambio, converter_totais
from app.models import Side
from app.positions.portfolio import PositionView, aggregate_by_ticker, data_quality


def test_converte_todos_os_blocos_pela_taxa_da_data() -> None:
    conversao = converter_totais(
        [("BRL", Decimal("100")), ("USD", Decimal("20"))],
        referencia=date(2026, 9, 18),
        taxa_usd_brl=TaxaDeCambio(Decimal("5.12"), date(2026, 9, 17)),
    )

    assert conversao.total == Decimal("202.40")
    assert conversao.taxa is not None
    assert conversao.taxa.data == date(2026, 9, 17)


def test_recusa_total_quando_a_taxa_ficou_velha() -> None:
    conversao = converter_totais(
        [("USD", Decimal("20"))],
        referencia=date(2026, 9, 18),
        taxa_usd_brl=TaxaDeCambio(Decimal("5.12"), date(2026, 9, 10)),
    )

    assert conversao.total is None
    assert "velha demais" in conversao.motivo


def test_recusa_total_quando_falta_taxa() -> None:
    conversao = converter_totais(
        [("BRL", Decimal("100")), ("USD", Decimal("20"))],
        referencia=date(2026, 9, 18),
        taxa_usd_brl=None,
    )

    assert conversao.total is None
    assert "sem taxa" in conversao.motivo


def test_agrega_o_mesmo_ticker_sem_fundir_as_posicoes() -> None:
    def position(broker: str, quantity: str, current: str) -> PositionView:
        record = SimpleNamespace(
            portfolio_id=7,
            currency="USD",
            ticker_id=12,
            ticker="BRK.B",
            portfolio_ref=SimpleNamespace(name="Exterior"),
            simulated=False,
            quantity=Decimal(quantity),
            side=Side.BUY,
            broker=broker,
        )
        metrics = SimpleNamespace(
            build_value=Decimal(quantity) * Decimal("400"),
            unwind_value=Decimal(current),
            result=Decimal(current) - Decimal(quantity) * Decimal("400"),
        )
        return PositionView(record, metrics, None, None, "online", "", "")

    groups = aggregate_by_ticker([position("Avenue", "2", "900"), position("Nomad", "3", "1350")])

    assert len(groups) == 1
    assert groups[0].net_quantity == Decimal("5")
    assert groups[0].current_total == Decimal("2250")
    assert groups[0].brokers == ("Avenue", "Nomad")


def test_qualidade_so_marca_pendencia_quando_a_cotacao_exige_atencao() -> None:
    view = SimpleNamespace(metrics=object(), quote_status="online")
    stale = SimpleNamespace(metrics=object(), quote_status="stale")
    missing = SimpleNamespace(metrics=None, quote_status="missing")

    quality = data_quality([view, stale, missing])

    assert quality.positions == 3
    assert quality.quoted == 2
    assert quality.stale == 1
    assert quality.missing == 1
    assert quality.errors == 0
