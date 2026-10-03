"""O contrato `patrimonio/v4` que este sistema publica para o consolidador.

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

Os contratos `patrimonio/v1` a `v3` (resumo, dashboard, atividades, categorias,
renda, desempenho, eventos e histórico por posição) foram retirados em
03/10/2026. O que valia para eles e continua valendo para o v4 está aqui.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from werkzeug.exceptions import HTTPException

from app.models import (
    Broker,
    Dividend,
    Market,
    Portfolio,
    Position,
    Quote,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
)
from app.patrimonio.fotografia import identidade
from app.routes import patrimonio as patrimonio_route

ROTA = "/patrimonio/v4/snapshot"
TOKEN = "token-de-teste-com-mais-de-trinta-e-dois-caracteres"
TOKEN_V4 = "token-de-integracao-v4-com-mais-de-trinta-e-dois-caracteres"
TOKEN_V4_ROTACIONADO = "novo-token-de-integracao-v4-apos-rotacao-segura"


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
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = ""

    assert client.get(ROTA).status_code == 503


def test_sem_token_apresentado_recusa(client, app):
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    app.config["PATRIMONIO_TITULAR"] = "Mariano"

    assert client.get(ROTA).status_code == 401


def test_token_errado_recusa(client, app):
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    app.config["PATRIMONIO_TITULAR"] = "Mariano"

    resposta = client.get(ROTA, headers={"Authorization": "Bearer outro-token-qualquer"})

    assert resposta.status_code == 401


def test_sem_titular_configurado_a_rota_nao_publica(client, app):
    """Ela não adivinha de quem é o dinheiro.

    O nome do usuário do aplicativo está ali, à mão, e usá-lo seria o caminho
    fácil -- e erraria a atribuição no dia em que duas pessoas usassem o mesmo
    login, sem nada apontar para a causa.
    """
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    app.config["PATRIMONIO_TITULAR"] = ""

    resposta = client.get(ROTA, headers={"Authorization": f"Bearer {TOKEN_V4}"})

    assert resposta.status_code == 503


def test_sem_owner_configurado_a_rota_nao_publica(client, app):
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    app.config["PATRIMONIO_TITULAR"] = "Mariano"
    app.config["PATRIMONIO_OWNER_ID"] = ""

    resposta = client.get(ROTA, headers={"Authorization": f"Bearer {TOKEN_V4}"})

    assert resposta.status_code == 503


def test_as_rotas_estao_declaradas_como_publicas():
    """Elas são máquina a máquina e não têm sessão -- mas a declaração é explícita."""
    from app import PUBLIC_ENDPOINTS

    assert {
        "portfolio.patrimonio_snapshot_v4",
        "portfolio.patrimonio_metadata_v4",
        "portfolio.patrimonio_changes_v4",
        "portfolio.patrimonio_activities_v4",
        "portfolio.patrimonio_ledger_v4",
    } <= PUBLIC_ENDPOINTS


def test_metodo_diferente_de_get_nao_existe(client, app):
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4

    assert client.post(ROTA).status_code == 405


@pytest.mark.parametrize(
    "rota",
    [
        "/patrimonio/v1/resumo",
        "/patrimonio/v2/resumo",
        "/patrimonio/v3/activities",
        "/patrimonio/v3/categories",
        "/patrimonio/v3/metadata",
        "/patrimonio/v3/income",
        "/patrimonio/v3/performance",
        "/patrimonio/v3/events",
        "/patrimonio/v3/holding-history",
    ],
)
def test_os_contratos_retirados_nao_existem(client, app, rota):
    """Retirados em 03/10/2026: nem com o token certo há o que responder."""
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4

    for token in (TOKEN, TOKEN_V4):
        resposta = client.get(rota, headers={"Authorization": f"Bearer {token}"})
        assert resposta.status_code in {302, 401, 404}, (rota, resposta.status_code)
        assert resposta.get_data(as_text=True).find("posicoes") < 0


def test_v4_nao_usa_o_token_das_rotas_anteriores(client, app):
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = ""

    resposta = client.get(
        "/patrimonio/v4/snapshot", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert resposta.status_code == 503


def test_v4_aceita_somente_o_token_de_integracao(client, app):
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    app.config["PATRIMONIO_TITULAR"] = ""

    token_legado = client.get(
        "/patrimonio/v4/snapshot", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    token_v4 = client.get(
        "/patrimonio/v4/snapshot", headers={"Authorization": f"Bearer {TOKEN_V4}"}
    )

    assert token_legado.status_code == 401
    assert token_v4.status_code == 503
    assert b"sem titular" in token_v4.data


def test_cursor_v4_usa_token_exclusivo_e_invalida_assinaturas_antigas(app):
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4

    with app.app_context():
        # A assinatura binária pode conter o byte usado como separador; o
        # parser separa pelo tamanho fixo do HMAC, não pelo último '.'.
        for position in range(200):
            cursor = patrimonio_route._cursor_v4(position, owner_id=17)
            assert patrimonio_route._cursor_v4_ler(cursor, owner_id=17) == position

        cursor_v4 = patrimonio_route._cursor_v4(41, owner_id=17)
        assert patrimonio_route._cursor_v4_ler(cursor_v4, owner_id=17) == 41

        app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4_ROTACIONADO
        with pytest.raises(HTTPException) as rotacionado:
            patrimonio_route._cursor_v4_ler(cursor_v4, owner_id=17)
        assert rotacionado.value.code == 400

        # Simula o formato assinado pela implementação anterior com
        # PATRIMONIO_TOKEN; a credencial exclusiva vigente o rejeita.
        app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN
        cursor_legado = patrimonio_route._cursor_v4(42, owner_id=17)
        app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
        with pytest.raises(HTTPException) as legado:
            patrimonio_route._cursor_v4_ler(cursor_legado, owner_id=17)
        assert legado.value.code == 400


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
def publicando(app_com_banco, cenario):
    app_com_banco.config["PATRIMONIO_TOKEN"] = TOKEN
    app_com_banco.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    app_com_banco.config["PATRIMONIO_TITULAR"] = "Mariano"
    app_com_banco.config["PATRIMONIO_OWNER_ID"] = str(cenario["usuario"].id)
    return app_com_banco.test_client()


def pedir_snapshot_v4(publicando, **parametros):
    return publicando.get(
        "/patrimonio/v4/snapshot",
        query_string=parametros,
        headers={"Authorization": f"Bearer {TOKEN_V4}"},
    )


def pedir_activities_v4(publicando, **parametros):
    return publicando.get(
        "/patrimonio/v4/activities",
        query_string=parametros,
        headers={"Authorization": f"Bearer {TOKEN_V4}"},
    )


@banco
def test_snapshot_v4_identifica_explicitamente_cada_holding_como_equity(
    sessao, cenario, publicando
):
    """O consumidor precisa reconhecer o tipo sem inferi-lo pelo ticker ou rota."""
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.add(_posicao(cenario, cenario["simulada"], quantity=Decimal("1000")))
    sessao.flush()

    resposta = pedir_snapshot_v4(publicando)

    assert resposta.status_code == 200
    assert resposta.headers["Cache-Control"] == "no-store"
    corpo = resposta.get_json()
    assert corpo["contrato"] == "patrimonio/v4"
    assert corpo["generated_at"] == corpo["gerado_em"]
    assert corpo["coverage"]["holdings"]["complete"] is True
    (holding,) = corpo["holdings"]
    assert holding["instrument"] == "WEGE3"
    assert holding["instrument_type"] == "equity"


@banco
def test_snapshot_v4_declara_cobertura_completa_mesmo_sem_posicoes(
    sessao, cenario, publicando
):
    resposta = pedir_snapshot_v4(publicando)

    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["holdings"] == []
    assert corpo["coverage"]["holdings"]["included"] == 0
    assert corpo["coverage"]["holdings"]["complete"] is True


@banco
def test_snapshot_v4_omite_precos_sem_omitir_posicoes(sessao, cenario, publicando):
    """O consumidor pode manter o retrato abaixo do limite de resposta da rede privada."""
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.flush()

    resposta = pedir_snapshot_v4(publicando, include_prices="false")

    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert len(corpo["holdings"]) == 1
    assert corpo["prices"] == {"current": [], "history": []}
    assert corpo["coverage"]["prices"]["complete"] is False
    assert corpo["coverage"]["prices"]["omitted"] == "consumer_requested"


@banco
def test_snapshot_v4_posicao_comprada_leva_valor_a_mercado_e_titular(sessao, cenario, publicando):
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.flush()

    corpo = pedir_snapshot_v4(publicando).get_json()

    (holding,) = corpo["holdings"]
    assert holding["account"] == "genial"
    assert holding["titular"] == "mariano"
    assert holding["currency"] == "BRL"
    assert holding["side"] == "long"
    assert Decimal(holding["market_value"]) == Decimal("15630.00")
    assert holding["price_observed_at"]


@banco
def test_snapshot_v4_posicao_vendida_entra_com_valor_negativo(sessao, cenario, publicando):
    """Uma posição vendida é um passivo a mercado, e o sinal diz isso."""
    sessao.add(_posicao(cenario, cenario["real"], side=Side.SELL))
    sessao.flush()

    (holding,) = pedir_snapshot_v4(publicando).get_json()["holdings"]

    assert holding["side"] == "short"
    assert Decimal(holding["quantity"]) == Decimal("-300")
    assert Decimal(holding["market_value"]) == Decimal("-15630.00")


@banco
def test_snapshot_v4_posicao_sem_cotacao_nao_vira_valor_inventado(sessao, cenario, publicando):
    sem_cotacao = Ticker(
        symbol="SEMQ3",
        trading_name="Sem cotação S.A.",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    sessao.add(sem_cotacao)
    sessao.flush()
    sessao.add(_posicao(cenario, cenario["real"], ticker_id=sem_cotacao.id))
    sessao.flush()

    (holding,) = pedir_snapshot_v4(publicando).get_json()["holdings"]

    assert holding["instrument"] == "SEMQ3"
    assert holding["current_price"] is None
    assert holding["market_value"] is None
    assert holding["price_kind"] == "unavailable"


@banco
def test_snapshot_v4_nenhum_valor_viaja_como_numero_json(sessao, cenario, publicando):
    """`float` não representa 0,10, e quem consolida somaria centavos que nunca
    existiram."""
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.flush()

    (holding,) = pedir_snapshot_v4(publicando).get_json()["holdings"]

    for campo in ("quantity", "average_cost", "current_price", "market_value"):
        assert isinstance(holding[campo], str), campo


@banco
def test_snapshot_v4_nao_vaza_outro_owner(sessao, cenario, publicando):
    outro = User(username="outro-dono", password_hash="hash")
    outra_carteira = Portfolio(
        name="Carteira privada de outro",
        owner_ref=outro,
        currency="BRL",
        simulated=False,
    )
    outro_papel = Ticker(
        symbol="OUTR3",
        trading_name="Outro ativo",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    sessao.add_all([outro, outra_carteira, outro_papel])
    sessao.flush()
    sessao.add_all(
        [
            Quote(
                ticker_id=outro_papel.id,
                last_price=Decimal("99.00"),
                previous_close=Decimal("98.00"),
                source_status="online",
                observed_at=datetime.now(UTC),
            ),
            _posicao(
                cenario,
                outra_carteira,
                owner_id=outro.id,
                ticker_id=outro_papel.id,
            ),
            Dividend(
                owner_id=outro.id,
                broker_id=cenario["corretora"].id,
                ticker_id=outro_papel.id,
                amount=Decimal("999.99"),
                payment_date=date.today(),
            ),
            _posicao(cenario, cenario["real"]),
        ]
    )
    sessao.flush()

    corpo = pedir_snapshot_v4(publicando).get_json()

    assert {h["instrument"] for h in corpo["holdings"]} == {"WEGE3"}
    assert all(p["instrument"] != "OUTR3" for p in corpo["prices"]["current"])
    assert all(p["instrument"] != "OUTR3" for p in corpo["prices"]["history"])
    assert all(i["instrument"] != "OUTR3" for i in corpo["income"])


def test_ledger_sem_sessao_responde_pelo_token_da_integracao(client, app):
    app.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    resposta = client.get("/patrimonio/v4/ledger")

    assert resposta.status_code == 401
    assert resposta.location is None


@banco
def test_activities_v4_pagina_resumos_encerrados_e_proventos(
    sessao, cenario, publicando, client
):
    watermark_antes = patrimonio_route._watermark_v4()
    cursor_antes = patrimonio_route._cursor_v4(watermark_antes, cenario["usuario"].id)
    sessao.add_all([
        Transaction(
            owner_id=cenario["usuario"].id,
            broker_id=cenario["corretora"].id,
            ticker_id=cenario["papel"].id,
            portfolio_id=cenario["real"].id,
            quantity=Decimal("2"),
            average_cost=Decimal("10"),
            exit_price=Decimal("12"),
            side=Side.BUY,
            opened_on=date(2026, 1, 1),
            closed_on=date(2026, 2, 1),
            result=Decimal("4"),
            status=TransactionStatus.CLOSED,
        ),
        Dividend(
            owner_id=cenario["usuario"].id,
            broker_id=cenario["corretora"].id,
            ticker_id=cenario["papel"].id,
            amount=Decimal("3.25"),
            payment_date=date(2026, 1, 15),
        ),
    ])
    sessao.flush()

    sem_token = client.get("/patrimonio/v4/activities")
    primeira = pedir_activities_v4(publicando, page_size=1)
    segunda = pedir_activities_v4(publicando, page_size=1, page=2)

    assert sem_token.status_code == 401
    assert sem_token.location is None
    assert primeira.status_code == segunda.status_code == 200
    corpo = primeira.get_json()
    assert corpo["contrato"] == "patrimonio/v4"
    assert corpo["coverage"]["complete"] is True
    assert corpo["coverage"]["closed_transactions_included"] == 1
    assert corpo["coverage"]["income_included"] == 1
    assert corpo["paginacao"]["total"] == 2
    assert corpo["paginacao"]["tem_proxima"] is True
    assert primeira.get_json()["itens"][0]["origem"] == "transacao"
    assert segunda.get_json()["itens"][0]["origem"] == "provento"
    assert segunda.get_json()["paginacao"]["tem_proxima"] is False
    alteracoes = publicando.get(
        "/patrimonio/v4/changes",
        query_string={"after": cursor_antes},
        headers={"Authorization": f"Bearer {TOKEN_V4}"},
    )
    assert alteracoes.status_code == 200
    assert {item["resource"] for item in alteracoes.get_json()["items"]} >= {
        "activity",
        "income",
    }
