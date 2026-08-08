from __future__ import annotations

import pytest
from flask.testing import FlaskClient

HTMX = {"HX-Request": "true"}

# Toda página cujo filtro troca só uma região: URL, id do alvo e o texto do
# rótulo que aparece no cabeçalho da página inteira mas não no fragmento.
FILTERED_PAGES = [
    ("/", "portfolio-results"),
    ("/analysis/exposure-asset", "exposure-results"),
    ("/analysis/exposure-broker", "exposure-results"),
    ("/analysis/exposure-market", "exposure-results"),
    ("/performance", "performance-results"),
    ("/quotes", "quotes-results"),
    ("/transactions", "transactions-results"),
    ("/dividends", "dividends-results"),
]


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.parametrize(("url", "_target"), FILTERED_PAGES)
def test_htmx_header_never_bypasses_authentication(
    client: FlaskClient, url: str, _target: str
) -> None:
    """`HX-Request` escolhe a forma da resposta, nunca concede acesso."""
    assert client.get(url, headers=HTMX).status_code == 302


@pytest.mark.parametrize(("url", "target"), FILTERED_PAGES)
def test_page_declares_the_swap_target(auth_client: FlaskClient, url: str, target: str) -> None:
    """O formulário de filtro precisa mirar uma região que exista na página;
    um alvo inexistente faria o HTMX não trocar nada, silenciosamente."""
    html = auth_client.get(url).get_data(as_text=True)

    assert f'id="{target}"' in html
    # Algumas páginas só desenham o filtro quando há o que filtrar (Cotações
    # esconde o seletor sem nenhum ticker cadastrado). Onde o formulário
    # aparece, ele precisa mirar a região certa e preservar o histórico.
    if 'class="filters' in html:
        assert f'hx-target="#{target}"' in html
        assert 'hx-push-url="true"' in html


@pytest.mark.parametrize(("url", "target"), FILTERED_PAGES)
def test_htmx_response_is_only_the_region(
    auth_client: FlaskClient, url: str, target: str
) -> None:
    fragment = auth_client.get(url, headers=HTMX).get_data(as_text=True)

    assert f'id="{target}"' in fragment
    assert "<!doctype html>" not in fragment.lower()
    assert "<nav" not in fragment.lower()
    assert "mega-wrap" not in fragment, "o fragmento não deve trazer a navegação"


def test_no_page_still_relies_on_the_javascript_auto_submit(
    auth_client: FlaskClient,
) -> None:
    """`data-auto-submit` era o gancho do submit manual em JavaScript; depois
    da conversão nenhuma página deve depender dele."""
    for url, _target in FILTERED_PAGES:
        assert "data-auto-submit" not in auth_client.get(url).get_data(as_text=True), url
