from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from flask import session

from app.privacy import VALUES_HIDDEN_SESSION_KEY

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


def test_todos_os_inputs_numericos_editaveis_tem_marcador_explicito():
    input_pattern = re.compile(r'<input\b[^>]*\btype="number"[^>]*>', re.IGNORECASE)
    unmarked: list[str] = []

    for path in TEMPLATES.rglob("*.html"):
        source = path.read_text(encoding="utf-8")
        for tag in input_pattern.findall(source):
            if 'data-sensitive-input="true"' not in tag:
                unmarked.append(f"{path.relative_to(ROOT)}: {tag}")

    assert not unmarked, "inputs numéricos sem marcador:\n" + "\n".join(unmarked)


def test_todos_os_outputs_com_class_number_tem_marcador_explicito():
    output_pattern = re.compile(
        r'<(?:td|strong|span)\b[^>]*\bclass="number\b[^>]*>', re.IGNORECASE
    )
    unmarked: list[str] = []

    for path in TEMPLATES.rglob("*.html"):
        source = path.read_text(encoding="utf-8")
        for tag in output_pattern.findall(source):
            if 'data-sensitive-value="true"' not in tag:
                unmarked.append(f"{path.relative_to(ROOT)}: {tag}")

    assert not unmarked, "outputs numéricos sem marcador:\n" + "\n".join(unmarked)


@pytest.mark.parametrize(
    ("template", "expressions"),
    [
        (
            "risk.html",
            ["metrics.observations|number(0)"],
        ),
        (
            "partials/options_results.html",
            ["m.elapsed_days|number(0)", "m.remaining_days|number(0)", "m.business_days|number(0)"],
        ),
        (
            "partials/portfolio_results.html",
            ["m.days|number(0)"],
        ),
        (
            "partials/transactions_results.html",
            ["tx.days_held|number(0)"],
        ),
    ],
)
def test_numeros_crus_que_eram_ocultados_no_navegador_sao_mascarados_no_servidor(
    template: str, expressions: list[str]
):
    source = (TEMPLATES / template).read_text(encoding="utf-8")

    for expression in expressions:
        assert expression in source


def test_outputs_financeiros_fora_de_class_number_tambem_sao_marcados():
    expected = {
        "partials/exposure.html": ["<strong data-sensitive-value=\"true\">"],
        "partials/quotes_results.html": [
            "<strong data-sensitive-value=\"true\">{{ entry.price|currency"
        ],
        "partials/transactions_results.html": [
            "<span data-sensitive-value=\"true\">{{ c.strike|currency"
        ],
    }

    for template, snippets in expected.items():
        source = (TEMPLATES / template).read_text(encoding="utf-8")
        for snippet in snippets:
            assert snippet in source, f"marcador ausente em {template}: {snippet}"


def test_modo_privacidade_tem_placeholder_css_e_inputs_de_encerramento_marcados():
    stylesheet = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")

    assert '[data-sensitive-value="true"]:not(input)::after' in stylesheet
    assert 'content: "****"' in stylesheet
    assert "pointer-events: none" in stylesheet
    for template in ("close_position_form.html", "close_option_form.html"):
        source = (TEMPLATES / template).read_text(encoding="utf-8")
        assert source.count('data-sensitive-input="true"') >= 4
