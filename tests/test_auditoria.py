"""A trilha de auditoria: o que ela registra, e o que ela nunca pode fazer.

Estes testes não tocam o banco — a suíte inteira não toca, por desenho. O que
eles protegem é decidido antes de qualquer consulta: quais entidades entram,
qual vocabulário vale, que texto de terceiro é saneado, e que registrar nunca
derruba a operação.
"""

from __future__ import annotations

from unittest import mock

import pytest

from app.auditoria import ACOES, ENTIDADES_FINANCEIRAS, _limpar, _resumo, registrar

# ---------------------------------------------------------------------------
# A trilha não pode derrubar a operação
# ---------------------------------------------------------------------------


def test_falha_ao_registrar_nao_interrompe_quem_chamou(app) -> None:
    """Perder o registro de um encerramento é ruim; impedir o encerramento é pior.

    Esta é a garantia mais importante do módulo: um defeito na trilha não pode
    virar um defeito na carteira.
    """
    with app.test_request_context("/"), mock.patch(
        "app.auditoria.db.session.add", side_effect=RuntimeError("banco fora")
    ):
        registrar("Position", "criar", entidade_id=1)  # não levanta


def test_registrar_nao_faz_commit(app) -> None:
    """A gravação participa da transação de quem chamou.

    Se o encerramento falhar e voltar atrás, a trilha não pode ficar afirmando
    que ele aconteceu.
    """
    with app.test_request_context("/"), mock.patch(
        "app.auditoria.db.session.add"
    ), mock.patch("app.auditoria.db.session.commit") as commit:
        registrar("Position", "criar", entidade_id=1)

    commit.assert_not_called()


def test_acao_fora_do_vocabulario_avisa_mas_registra(app) -> None:
    """Vocabulário desconhecido é defeito de programação, não de dado."""
    with app.test_request_context("/"), mock.patch(
        "app.auditoria.db.session.add"
    ) as add, mock.patch("app.auditoria._log") as log:
        registrar("Position", "acao-inventada", entidade_id=1)

    log.warning.assert_called_once()
    add.assert_called_once()


# ---------------------------------------------------------------------------
# Texto de terceiro
# ---------------------------------------------------------------------------


def test_detalhes_passam_por_sanitizar_log() -> None:
    """O login vem de formulário: sem isto, uma quebra de linha forja registro."""
    limpo = _limpar({"username": "joao\nINFO login aceito usuario=admin"})

    assert "\n" not in limpo["username"]


def test_a_chave_tambem_e_saneada() -> None:
    """Um dicionário montado de um formulário pode ter as duas pontas de fora."""
    limpo = _limpar({"campo\ninjetado": "valor"})

    assert all("\n" not in chave for chave in limpo)


def test_senha_em_detalhe_e_redigida() -> None:
    assert "hunter2" not in str(_limpar({"tentativa": "senha=hunter2"}))


def test_detalhes_vazios_viram_nulo() -> None:
    """Coluna nula é mais honesta que `{}` — e a consulta fica mais simples."""
    assert _limpar(None) is None
    assert _limpar({}) is None


# ---------------------------------------------------------------------------
# O que entra e o que fica de fora
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entidade", ["Position", "OptionPosition", "Transaction", "Dividend"]
)
def test_escrita_que_muda_a_carteira_entra(entidade: str) -> None:
    assert entidade in ENTIDADES_FINANCEIRAS


@pytest.mark.parametrize("entidade", ["Quote", "OptionQuote", "QuoteHistory"])
def test_o_que_a_coleta_escreve_fica_de_fora(entidade: str) -> None:
    """O agente entrega cotação a cada poucos segundos.

    Registrar isso afogaria a trilha com movimento que nenhuma pessoa fez, e a
    pergunta que a trilha responde é sobre pessoa.
    """
    assert entidade not in ENTIDADES_FINANCEIRAS


@pytest.mark.parametrize("entidade", ["Broker", "Ticker", "Portfolio", "AppSetting"])
def test_cadastro_fica_de_fora(entidade: str) -> None:
    assert entidade not in ENTIDADES_FINANCEIRAS


def test_toda_entidade_declarada_existe_de_verdade() -> None:
    """Um nome escrito errado desligaria o registro daquela entidade em silêncio."""
    import app.models as modelos

    faltando = [nome for nome in ENTIDADES_FINANCEIRAS if not hasattr(modelos, nome)]

    assert faltando == [], faltando


def test_o_resumo_nao_copia_o_registro_inteiro() -> None:
    """Guardar antes e depois de tudo faria a trilha crescer mais que os dados."""

    class _Posicao:
        id = 7
        ticker_id = 3
        broker_id = 2
        quantity = 100
        average_cost = "irrelevante para a trilha"
        notes = "tambem nao"

    resumo = _resumo(_Posicao())

    assert set(resumo) == {"ticker_id", "broker_id", "quantity"}


def test_os_campos_do_resumo_existem_nos_modelos_auditados() -> None:
    """`broker` nao existe em `Position` -- a coluna e `broker_id`.

    Um nome errado aqui nao quebra nada: o resumo sai vazio e a trilha perde a
    informacao que identificaria a linha, em silencio.
    """
    from app import models
    from app.auditoria import ENTIDADES_FINANCEIRAS

    campos = {"ticker_id", "portfolio_id", "broker_id", "side", "quantity", "status"}
    reconhecidos = {
        campo
        for nome in ENTIDADES_FINANCEIRAS
        for campo in campos
        if hasattr(getattr(models, nome), campo)
    }

    assert campos - reconhecidos == set(), sorted(campos - reconhecidos)


# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "acao",
    [
        "login",
        "login_recusado",
        "logout",
        "criar",
        "atualizar",
        "excluir",
        "redefinir_senha",
        "trocar_senha",
    ],
)
def test_vocabulario_cobre_o_que_os_pontos_de_registro_usam(acao: str) -> None:
    assert acao in ACOES


def test_os_pontos_de_registro_so_usam_o_vocabulario_declarado() -> None:
    """Uma ação inventada num ponto novo não se consulta junto com as outras."""
    import ast
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "app"
    usadas: set[str] = set()

    for caminho in raiz.rglob("*.py"):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if (
                isinstance(no, ast.Call)
                and isinstance(no.func, ast.Name)
                and no.func.id == "registrar"
                and len(no.args) >= 2
                and isinstance(no.args[1], ast.Constant)
                and isinstance(no.args[1].value, str)
            ):
                usadas.add(no.args[1].value)

    assert usadas, "nenhuma chamada a registrar() encontrada -- o teste parou de olhar"
    assert usadas <= ACOES, sorted(usadas - ACOES)


def test_a_senha_nunca_entra_na_trilha() -> None:
    """Não há pergunta que ela responda e há muitas que ela abre.

    Cobre as duas funções que mexem em senha: a redefinição feita por um
    administrador e a troca feita pelo próprio dono. O recorte é por função,
    e não um intervalo entre duas âncoras do arquivo: quando
    `change_own_password` nasceu entre `reset_password` e `set_active`, o
    intervalo antigo passou a incluí-la sem que nenhuma asserção a olhasse.
    """
    from pathlib import Path

    fonte = (
        Path(__file__).resolve().parent.parent / "app" / "user_management.py"
    ).read_text(encoding="utf-8")

    def corpo(nome: str) -> str:
        inicio = fonte.index(f"def {nome}")
        # `read_text` normaliza a quebra de linha, entao "\ndef " acha o
        # inicio da proxima funcao de topo tanto em LF quanto em CRLF.
        fim = fonte.find("\ndef ", inicio + 1)
        return fonte[inicio : fim if fim != -1 else len(fonte)]

    for nome in ("reset_password", "change_own_password"):
        trecho = corpo(nome)
        assert "registrar(" in trecho, f"{nome} deixou de registrar na trilha"
        argumentos = trecho.split("registrar(")[1].split(")")[0]
        assert "password" not in argumentos, f"{nome} leva senha para a trilha"

    assert 'detalhes={"username"' in corpo("reset_password")
