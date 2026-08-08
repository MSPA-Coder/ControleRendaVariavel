from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Market, QuoteHistory, Ticker

pytestmark = [pytest.mark.interface_smoke]


def _form_markup(html: str, opening: str) -> str:
    """Recorta um <form> do HTML, do atributo de abertura até o fechamento."""
    start = html.index(opening)
    return html[start : html.index("</form>", start)]


def _seed_two_tickers_with_history() -> None:
    petr = Ticker(
        symbol="PETR4",
        trading_name="Petrobras",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    vale = Ticker(
        symbol="VALE3",
        trading_name="Vale",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add_all([petr, vale])
    db.session.commit()
    for ticker, price in ((petr, "25.50"), (vale, "61.20")):
        db.session.add(
            QuoteHistory(
                ticker_id=ticker.id,
                price=Decimal(price),
                recorded_date=date(2026, 8, 3),
                recorded_at=datetime(2026, 8, 3, tzinfo=UTC),
            )
        )
    db.session.commit()


def test_quotes_ticker_filter_is_wired_to_htmx(app: Flask, auth_client: FlaskClient) -> None:
    """O seletor de ticker de Cotações só aparece quando há ticker cadastrado.
    Sem semear dados, a página não desenha o filtro e qualquer asserção sobre
    ele passa por vacuidade — foi assim que a conversão para HTMX deixou este
    formulário para trás, ainda dependendo de um JavaScript já removido."""
    with app.app_context():
        _seed_two_tickers_with_history()

    html = auth_client.get("/quotes").get_data(as_text=True)

    assert 'id="quote-ticker"' in html, "o filtro de ticker precisa estar na página"
    assert "data-auto-submit" not in html, (
        "data-auto-submit era o gancho do submit em JavaScript, que não existe mais"
    )
    # O controle precisa disparar a requisição: pelo próprio atributo ou pelo
    # formulário que o contém.
    start = html.index('id="quote-ticker"')
    ticker_control = html[start : html.index("</select>", start)]
    assert "hx-get" in ticker_control


def test_quotes_chart_period_does_not_trigger_a_server_request(
    app: Flask, auth_client: FlaskClient
) -> None:
    """O seletor de período redesenha o gráfico no cliente a partir de dados
    já embutidos. Se ele disparasse a troca de fragmento, cada mudança de
    período viraria ida ao servidor e perderia a seleção local."""
    with app.app_context():
        _seed_two_tickers_with_history()

    html = auth_client.get("/quotes").get_data(as_text=True)

    start = html.index('id="quote-period"')
    period = html[start : html.index("</select>", start)]
    assert "hx-get" not in period

    # O período pode continuar dentro do formulário — o que não pode é o
    # formulário disparar a cada `change`, porque o evento borbulha de
    # qualquer descendente, inclusive deste seletor.
    form_start = html.index('<form class="quote-ticker-filter"')
    form_tag = html[form_start : html.index(">", form_start)]
    assert "hx-trigger" not in form_tag, (
        "com o gatilho no formulário, mudar o período viraria requisição ao servidor"
    )


def test_rtd_toggle_is_not_inside_the_filter_form(auth_client: FlaskClient) -> None:
    """O controle do coletor não é um filtro: dentro do formulário que dispara
    a cada `change`, ligá-lo faria duas requisições e ainda mandaria `enabled`
    como parâmetro de filtro."""
    html = auth_client.get("/").get_data(as_text=True)

    filters = _form_markup(html, '<form class="filters header-controls"')

    assert "rtd-toggle" not in filters
    assert 'name="enabled"' not in filters


def test_dashboard_filters_stay_inside_the_form(auth_client: FlaskClient) -> None:
    """Contraprova do teste acima: os filtros de verdade continuam no
    formulário, senão o HTMX não os enviaria."""
    html = auth_client.get("/").get_data(as_text=True)

    filters = _form_markup(html, '<form class="filters header-controls"')

    assert 'name="position_kind"' in filters
    assert 'name="broker"' in filters
    assert 'name="group_by_broker"' in filters


@pytest.mark.business_rule
def test_quotes_filter_actually_selects_the_requested_ticker(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_two_tickers_with_history()
        vale_id = db.session.scalar(
            db.select(Ticker.id).where(Ticker.symbol == "VALE3")  # type: ignore[attr-defined]
        )

    html = auth_client.get(
        f"/quotes?ticker_id={vale_id}", headers={"HX-Request": "true"}
    ).get_data(as_text=True)

    assert "61,20" in html
    assert "25,50" not in html


class _TriggeringFormScanner(HTMLParser):
    """Coleta os controles que vivem dentro de um <form> com gatilho HTMX.

    Um `hx-trigger="change"` no formulário responde ao `change` de qualquer
    descendente. Controles que não são filtro — sem `name`, ou com nome que
    não é parâmetro de consulta — não podem ficar ali dentro.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.offenders: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self.depth = 1 if "hx-trigger" in attributes else 0
            return
        if self.depth and tag in {"select", "input", "textarea"} and not attributes.get("name"):
            self.offenders.append((tag, attributes))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.depth = 0


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/analysis/exposure-asset",
        "/analysis/exposure-broker",
        "/analysis/exposure-market",
        "/performance",
        "/quotes",
        "/transactions",
        "/dividends",
    ],
)
def test_no_unnamed_control_sits_inside_a_triggering_form(
    app: Flask, auth_client: FlaskClient, url: str
) -> None:
    """Guarda genérica contra a falha que quebrou Cotações: um controle sem
    `name` dentro de um formulário com gatilho dispara uma requisição que não
    filtra nada e ainda descarta o estado local do controle."""
    with app.app_context():
        _seed_two_tickers_with_history()

    scanner = _TriggeringFormScanner()
    scanner.feed(auth_client.get(url).get_data(as_text=True))

    assert not scanner.offenders, (
        f"{url}: controles sem name dentro de form com gatilho: {scanner.offenders}"
    )
