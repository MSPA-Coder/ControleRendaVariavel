"""A rota que publica o investimento deste sistema para o consolidador.

ESTE ARQUIVO PROTEGE UM CONTRATO, NÃO UMA TELA

O que sai daqui é lido por outro aplicativo, escrito em outro momento, por
alguém que não vai ler este código. Contrato quebrado não dá erro: dá número
errado do outro lado.

Duas coisas dominam o arquivo, e as duas são sobre **não inflar patrimônio**:

- **carteira simulada não é patrimônio.** Metade das posições da base real está
  numa. Publicá-las dobraria o número, e ele continuaria parecendo plausível;
- **titular aqui não é o que titular significa lá.** `owner_id` aponta para o
  usuário do aplicativo; do outro lado é a pessoa dona do dinheiro. Publicar um
  como se fosse o outro atribuiria patrimônio à pessoa errada no dia em que
  deixassem de coincidir -- por isso a rota exige a configuração e não adivinha.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models import Broker, Market, Portfolio, Position, Quote, Side, Ticker, User
from app.routes.patrimonio import identidade

ROTA = "/patrimonio/v1/resumo"
TOKEN = "token-de-teste-com-mais-de-trinta-e-dois-caracteres"


# ---------------------------------------------------------------------------
# O vocabulário comum -- sem banco
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Mercado Pago", "mercado-pago"),
        ("mercado  pago", "mercado-pago"),
        ("Itaú", "itau"),
        ("Banco do Brasil", "banco-do-brasil"),
        ("C6", "c6"),
    ],
)
def test_identidade_normaliza_o_nome(nome, esperado):
    """Os MESMOS casos existem no Controle Bancário, e é de propósito.

    Se os dois lados normalizarem diferente, "Itaú" e "itau" viram dois
    titulares e o patrimônio aparece dobrado. Enquanto a função não subir para o
    `sharedauth`, é este par de testes que impede as duas cópias de derivarem.
    """
    assert identidade(nome) == esperado


# ---------------------------------------------------------------------------
# A chave é a permissão -- sem banco
# ---------------------------------------------------------------------------


def test_sem_token_configurado_a_rota_nao_atende(client, app):
    """"Não configurado" não é "não autorizado".

    Responder 401 aqui mandaria o operador procurar por horas um token que
    nunca foi concedido a este servidor.
    """
    app.config["PATRIMONIO_TOKEN"] = ""

    assert client.get(ROTA).status_code == 503


def test_sem_token_apresentado_recusa(client, app):
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    app.config["PATRIMONIO_TITULAR"] = "Mariano"

    assert client.get(ROTA).status_code == 401


def test_token_errado_recusa(client, app):
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    app.config["PATRIMONIO_TITULAR"] = "Mariano"

    resposta = client.get(ROTA, headers={"Authorization": "Bearer outro-token-qualquer"})

    assert resposta.status_code == 401


def test_sem_titular_configurado_a_rota_nao_publica(client, app):
    """Ela não adivinha de quem é o dinheiro.

    O nome do usuário do aplicativo está ali, à mão, e usá-lo seria o caminho
    fácil -- e erraria a atribuição no dia em que duas pessoas usassem o mesmo
    login, sem nada apontar para a causa.
    """
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    app.config["PATRIMONIO_TITULAR"] = ""

    resposta = client.get(ROTA, headers={"Authorization": f"Bearer {TOKEN}"})

    assert resposta.status_code == 503


def test_a_rota_esta_declarada_como_publica():
    """Ela é máquina a máquina e não tem sessão -- mas a declaração é explícita."""
    from app import PUBLIC_ENDPOINTS

    assert "portfolio.patrimonio_resumo" in PUBLIC_ENDPOINTS


def test_metodo_diferente_de_get_nao_existe(client, app):
    app.config["PATRIMONIO_TOKEN"] = TOKEN

    assert client.post(ROTA).status_code == 405


# ---------------------------------------------------------------------------
# O conteúdo -- com banco
# ---------------------------------------------------------------------------

banco = pytest.mark.banco


@pytest.fixture
def cenario(sessao):
    """Uma carteira real e uma simulada, com uma posição em cada."""
    usuario = User(username="dono", password_hash="hash")
    corretora = Broker(name="Genial", acronym="GNL")
    papel = Ticker(
        symbol="WEGE3",
        trading_name="WEG S.A.",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    real = Portfolio(name="BRL", owner_ref=usuario, currency="BRL", simulated=False)
    simulada = Portfolio(name="Simulada", owner_ref=usuario, currency="BRL", simulated=True)
    sessao.add_all([usuario, corretora, papel, real, simulada])
    sessao.flush()
    sessao.add(
        Quote(
            ticker_id=papel.id,
            last_price=Decimal("52.10"),
            previous_close=Decimal("51.00"),
            source_status="online",
            observed_at=datetime.now(UTC),
        )
    )
    sessao.flush()
    return {
        "usuario": usuario,
        "corretora": corretora,
        "papel": papel,
        "real": real,
        "simulada": simulada,
    }


def _posicao(cenario, carteira, **campos):
    padrao = {
        "broker_id": cenario["corretora"].id,
        "ticker_id": cenario["papel"].id,
        "portfolio_id": carteira.id,
        "owner_id": cenario["usuario"].id,
        "quantity": Decimal("300"),
        "average_cost": Decimal("40.00"),
        "side": Side.BUY,
        "opened_on": date(2026, 1, 10),
    }
    padrao.update(campos)
    return Position(**padrao)


@pytest.fixture
def publicando(app_com_banco):
    app_com_banco.config["PATRIMONIO_TOKEN"] = TOKEN
    app_com_banco.config["PATRIMONIO_TITULAR"] = "Mariano"
    return app_com_banco.test_client()


def pedir(publicando, **parametros):
    return publicando.get(
        ROTA, query_string=parametros, headers={"Authorization": f"Bearer {TOKEN}"}
    )


@banco
def test_posicao_real_e_publicada_com_valor_a_mercado(sessao, cenario, publicando):
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.flush()

    corpo = pedir(publicando).get_json()

    assert corpo["contrato"] == "patrimonio/v1"
    assert corpo["sistema"] == "controle-renda-variavel"
    assert corpo["papel"] == "investimento"
    (linha,) = corpo["posicoes"]
    assert linha["instrumento"] == "WEGE3"
    assert linha["instituicao"] == "genial"
    assert linha["titular"] == "mariano"
    assert linha["moeda"] == "BRL"
    assert Decimal(linha["valor_a_mercado"]) == Decimal("15630.00")
    assert linha["preco_em"]
    assert linha["fonte_do_preco"]


@banco
def test_carteira_simulada_fica_de_fora_e_a_omissao_e_contada(sessao, cenario, publicando):
    """O teste central deste arquivo.

    Somar uma posição simulada ao patrimônio o infla com dinheiro que não
    existe, e o número continua parecendo plausível -- ninguém desconfia.
    """
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.add(_posicao(cenario, cenario["simulada"], quantity=Decimal("1000")))
    sessao.flush()

    corpo = pedir(publicando).get_json()

    assert len(corpo["posicoes"]) == 1
    assert corpo["omitidas"]["simuladas"] == 1
    assert Decimal(corpo["totais_por_moeda"][0]["total"]) == Decimal("15630.00")


@banco
def test_posicao_sem_cotacao_nao_vira_valor_inventado(sessao, cenario, publicando):
    outro = Ticker(
        symbol="SEMQ3",
        trading_name="Sem cotação S.A.",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    sessao.add(outro)
    sessao.flush()
    sessao.add(_posicao(cenario, cenario["real"], ticker_id=outro.id))
    sessao.flush()

    corpo = pedir(publicando).get_json()

    assert corpo["posicoes"] == []
    assert corpo["omitidas"]["sem_cotacao"] == 1


@banco
def test_posicao_vendida_entra_com_valor_negativo(sessao, cenario, publicando):
    """Uma posição vendida é um passivo a mercado, e o sinal diz isso."""
    sessao.add(_posicao(cenario, cenario["real"], side=Side.SELL))
    sessao.flush()

    corpo = pedir(publicando).get_json()

    (linha,) = corpo["posicoes"]
    assert Decimal(linha["valor_a_mercado"]) == Decimal("-15630.00")


@banco
def test_nenhum_valor_viaja_como_numero_json(sessao, cenario, publicando):
    """`float` não representa 0,10, e quem consolida somaria centavos que nunca
    existiram."""
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.flush()

    corpo = pedir(publicando).get_json()

    for linha in corpo["posicoes"]:
        assert isinstance(linha["valor_a_mercado"], str)
        assert isinstance(linha["quantidade"], str)
        assert isinstance(linha["preco"], str)
    for total in corpo["totais_por_moeda"]:
        assert isinstance(total["total"], str)


@banco
def test_o_envelope_traz_as_listas_que_o_outro_sistema_preenche(sessao, cenario, publicando):
    """Lista vazia diz "não tenho nada a dizer"; chave ausente não diz nada.

    `contas` é do Controle Bancário: o caixa das corretoras mora lá desde a
    decisão de 16/09/2026.
    """
    corpo = pedir(publicando).get_json()

    assert corpo["contas"] == []
    assert corpo["ativos_alternativos"] == []


@banco
def test_data_passada_e_recusada_em_vez_de_respondida_errado(sessao, cenario, publicando):
    """Aplicar a cotação de hoje a uma carteira de março inventaria o passado."""
    resposta = pedir(publicando, data="2026-03-31")

    assert resposta.status_code == 400


@banco
def test_data_invalida_e_recusada(sessao, cenario, publicando):
    assert pedir(publicando, data="31/03/2026").status_code == 400


@banco
def test_a_resposta_nao_pode_ser_guardada_por_intermediario(sessao, cenario, publicando):
    assert pedir(publicando).headers["Cache-Control"] == "no-store"
