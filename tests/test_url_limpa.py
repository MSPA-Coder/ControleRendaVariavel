"""O endereço que chega à barra depois de uma atualização por HTMX.

O que estes testes protegem: a barra mostra os filtros que alguém escolheu, e
só eles. Nem o ruído dos campos no padrão (que o formulário envia sempre), nem
o estado de linha aberta (que viaja na requisição do fragmento).
"""

from __future__ import annotations

import pytest

from app.url_limpa import ENDPOINTS_COM_BARRA, FILTROS_PADRAO, url_canonica


def _em(app, caminho: str, endpoint: str):
    """Contexto de requisição fingindo ter resolvido para ``endpoint``."""
    contexto = app.test_request_context(caminho)
    contexto.request.url_rule = type("Regra", (), {"endpoint": endpoint})()
    return contexto


# --------------------------------------------------------------------------
# url_canonica
# --------------------------------------------------------------------------


def test_sem_filtro_o_endereco_fica_limpo(app) -> None:
    with _em(app, "/?portfolio_id=all&broker=&return_days=365", "portfolio.index"):
        assert url_canonica() == "/"


def test_filtro_escolhido_permanece(app) -> None:
    with _em(app, "/?portfolio_id=all&broker=XP&return_days=365", "portfolio.index"):
        assert url_canonica() == "/?broker=XP"


def test_varios_filtros_escolhidos_permanecem(app) -> None:
    with _em(app, "/?portfolio_id=7&broker=XP&return_days=30", "portfolio.index"):
        destino = url_canonica()
        assert "portfolio_id=7" in destino
        assert "broker=XP" in destino
        assert "return_days=30" in destino


def test_estado_de_interface_nunca_chega_a_barra(app) -> None:
    """`expanded*` descreve como a tela está desenhada, não quais dados mostra.

    Ele viaja na requisição do fragmento (o botão `+` de cada linha) e mudaria
    a cada clique; um link com ele dentro não serve para ninguém.
    """
    with _em(
        app,
        "/dividends?broker=XP&expanded_tickers=12,47&expanded_years=2025-BRL",
        "portfolio.dividends",
    ):
        assert url_canonica() == "/dividends?broker=XP"


def test_endpoint_que_nao_e_tela_inteira_nao_mexe_na_barra(app) -> None:
    """Fragmento do HTMX não pode virar o endereço da página."""
    with _em(app, "/partials/heartbeat", "portfolio.collector_heartbeat_partial"):
        assert url_canonica() is None


def test_parametro_desconhecido_e_preservado(app) -> None:
    """Falha visível em vez de silenciosa.

    Um filtro novo que alguém acrescente sem lembrar de `FILTROS_PADRAO`
    continua funcionando na barra. Descartá-lo o faria sumir do endereço em
    silêncio, quebrando favorito e link sem nada apontar para a causa.
    """
    with _em(app, "/?filtro_novo=42", "portfolio.index"):
        assert url_canonica() == "/?filtro_novo=42"


@pytest.mark.parametrize(
    ("caminho", "endpoint"),
    [
        ("/transactions?portfolio_id=all&broker=", "portfolio.transactions"),
        ("/dividends?broker=", "portfolio.dividends"),
        ("/options?portfolio_id=all&broker=", "options.index"),
        ("/performance?period=all&portfolio=stocks&broker=", "portfolio.monthly_performance"),
    ],
)
def test_cada_tela_abre_com_endereco_sem_parametro(app, caminho: str, endpoint: str) -> None:
    with _em(app, caminho, endpoint):
        assert "?" not in url_canonica()


# --------------------------------------------------------------------------
# Coerência das tabelas
# --------------------------------------------------------------------------


def test_endpoints_declarados_existem_de_verdade(app) -> None:
    """Um endpoint escrito errado desligaria a limpeza sem nada reclamar."""
    registrados = set(app.view_functions)
    assert registrados >= ENDPOINTS_COM_BARRA, (
        f"endpoints inexistentes: {sorted(ENDPOINTS_COM_BARRA - registrados)}"
    )


def test_padrao_do_status_acompanha_o_dominio() -> None:
    """`FILTROS_PADRAO` copia padrões lidos nas rotas; este é o que vem de enum.

    Se `TransactionStatus.CLOSED` mudar de valor, a cópia aqui precisa mudar
    junto -- senão `status` volta a aparecer na barra sem ninguém ter escolhido.
    """
    from app.models import TransactionStatus

    assert FILTROS_PADRAO["status"] == TransactionStatus.CLOSED.value
