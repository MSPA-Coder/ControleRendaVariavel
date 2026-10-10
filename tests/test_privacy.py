from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from flask import session

from app.core.privacy import VALUES_HIDDEN_SESSION_KEY

ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "app" / "templates"


def _render(app, source: str, *, hidden: bool, **context: object) -> str:
    with app.test_request_context("/"):
        session[VALUES_HIDDEN_SESSION_KEY] = hidden
        # Renderiza diretamente pelo ambiente Jinja para não executar os
        # context processors da aplicação. Alguns deles consultam o heartbeat
        # no PostgreSQL, enquanto estes testes verificam somente a camada de
        # apresentação e devem permanecer sem banco.
        return app.jinja_env.from_string(source).render(**context)


@pytest.mark.parametrize(
    ("expression", "context", "expected"),
    [
        ("{{ value|money }}", {"value": Decimal("1234.56")}, "****"),
        ("{{ value|currency('BRL') }}", {"value": Decimal("1234.56")}, "****"),
        ("{{ value|quantity('BRL') }}", {"value": Decimal("1234")}, "****"),
        ("{{ value|number(2) }}", {"value": Decimal("12.34")}, "****"),
        ("{{ value|percent(2) }}", {"value": Decimal("0.1234")}, "****"),
        (
            "{{ text|privacy_text }}",
            {"text": "Falha: R$ 1.234,56"},
            "Falha: ****",
        ),
    ],
)
def test_filtros_de_apresentacao_mascaram_valores_no_modo_discreto(
    app, expression: str, context: dict[str, object], expected: str
):
    assert _render(app, expression, hidden=True, **context) == expected


def test_filtros_preservam_a_apresentacao_normal_fora_do_modo_discreto(app):
    rendered = _render(
        app,
        "{{ amount|currency('BRL') }} · {{ rate|percent(2) }}",
        hidden=False,
        amount=Decimal("1234.56"),
        rate=Decimal("0.1234"),
    )

    assert "R$ 1.234,56" in rendered
    assert "12,34%" in rendered


def test_filtros_cobrem_zero_negativo_e_nulo_sem_alterar_o_modo_normal(app):
    assert _render(app, "{{ value|currency('BRL') }}", hidden=True, value=Decimal("0")) == "****"
    assert _render(app, "{{ value|currency('BRL') }}", hidden=True, value=Decimal("-12.34")) == "****"
    assert _render(app, "{{ value|currency('BRL') }}", hidden=True, value=None) == "-"
    assert _render(app, "{{ value|currency('BRL') }}", hidden=False, value=Decimal("-12.34")) == "R$ -12,34"


def test_renderizacao_minima_mascara_output_financeiro_e_preserva_marcador(app):
    rendered = _render(
        app,
        '<td class="number" data-sensitive-value="true">'
        "{{ amount|currency('BRL') }}</td>",
        hidden=True,
        amount=Decimal("1234.56"),
    )

    assert 'data-sensitive-value="true"' in rendered
    assert "****" in rendered
    assert "1.234,56" not in rendered
