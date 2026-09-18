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

from app import login_manager
from app.models import (
    ROLE_ADMIN,
    Broker,
    Dividend,
    Market,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    PositionMovementKind,
    Quote,
    QuoteHistory,
    Side,
    Ticker,
    User,
)
from app.routes.patrimonio import identidade

ROTA = "/patrimonio/v1/resumo"
ROTA_V2 = "/patrimonio/v2/resumo"
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


def test_v2_exige_o_mesmo_bearer_e_so_responde_get(client, app):
    app.config["PATRIMONIO_TOKEN"] = TOKEN
    app.config["PATRIMONIO_TITULAR"] = "Mariano"

    assert client.get(ROTA_V2).status_code == 401
    assert client.post(ROTA_V2, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 405


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


def pedir_v2(publicando, **parametros):
    return publicando.get(
        ROTA_V2, query_string=parametros, headers={"Authorization": f"Bearer {TOKEN}"}
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
def test_v2_publica_snapshot_enriquecido_sem_simulada(sessao, cenario, publicando):
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.add(_posicao(cenario, cenario["simulada"], quantity=Decimal("1000")))
    sessao.flush()

    resposta = pedir_v2(publicando, periodo="year")
    corpo = resposta.get_json()

    assert resposta.status_code == 200
    assert resposta.headers["Cache-Control"] == "no-store"
    assert corpo["contrato"] == "patrimonio/v2"
    assert corpo["periodo"]["nome"] == "year"
    assert corpo["qualidade"]["status"] == "ok"
    assert len(corpo["posicoes"]) == 1
    (linha,) = corpo["posicoes_atuais"]
    assert linha["instituicao"] == "genial"
    assert linha["custo_total"] == "12000.00"
    # A posicao usa o modo liquido por padrao, portanto o contrato publica o
    # mesmo resultado ajustado que as telas do sistema, nao o ganho bruto.
    assert linha["resultado_nao_realizado"] == "3628.55"
    assert corpo["omitidas"]["simuladas"] == 1
    assert all(not carteira["simulada"] for carteira in corpo["carteiras"])


@banco
def test_v2_historico_usa_quantidade_da_data_e_nao_a_atual(
    sessao, cenario, com_extrato, publicando
):
    corpo = pedir_v2(
        publicando,
        data="2026-02-14",
        inicio="2026-02-01",
        periodo="week",
    ).get_json()

    assert corpo["periodo"] == {
        "nome": "week",
        "inicio": "2026-02-01",
        "fim": "2026-02-14",
    }
    (linha,) = corpo["posicoes_atuais"]
    assert linha["quantidade"] == "100"
    assert linha["valor_a_mercado"] == "4000.00"
    assert linha["custo_total"] is None
    assert linha["motivo_valores"] == "custo_historico_nao_confiavel"


@banco
def test_data_invalida_e_recusada(sessao, cenario, publicando):
    assert pedir(publicando, data="31/03/2026").status_code == 400


# ---------------------------------------------------------------------------
# Uma data passada -- com banco
#
# A pergunta é outra: a quantidade vem do extrato e o preço vem do fechamento
# daquele dia. A cotação ao vivo do cenário (52,10) aparece de propósito: se
# ela vazar para uma data passada, o valor esperado não fecha.
# ---------------------------------------------------------------------------


def _movimento(cenario, posicao, dia, kind, delta, resultante):
    return PositionMovement(
        owner_id=cenario["usuario"].id,
        position_id=posicao.id,
        kind=kind,
        quantity_delta=Decimal(delta),
        price=Decimal("40"),
        occurred_on=dia,
        result=Decimal("0") if kind == PositionMovementKind.DECREASE else None,
        resulting_quantity=Decimal(resultante),
        resulting_average_cost=Decimal("40"),
    )


def _fechamento(cenario, dia, preco, ticker=None):
    return QuoteHistory(
        ticker_id=(ticker or cenario["papel"]).id,
        price=Decimal(preco),
        recorded_date=dia,
        recorded_at=datetime.combine(dia, datetime.min.time(), tzinfo=UTC),
    )


@pytest.fixture
def com_extrato(sessao, cenario):
    """100 ações em 10/01, 300 a partir de 01/03; fechamentos em fevereiro e março."""
    posicao = _posicao(cenario, cenario["real"], opened_on=date(2026, 1, 10))
    sessao.add(posicao)
    sessao.flush()
    sessao.add_all(
        [
            _movimento(cenario, posicao, date(2026, 1, 10), PositionMovementKind.OPEN, "100", "100"),
            _movimento(
                cenario, posicao, date(2026, 3, 1), PositionMovementKind.INCREASE, "200", "300"
            ),
            _fechamento(cenario, date(2026, 2, 12), "39"),
            _fechamento(cenario, date(2026, 2, 13), "40"),
            _fechamento(cenario, date(2026, 3, 31), "45"),
        ]
    )
    sessao.flush()
    return posicao


@banco
def test_data_passada_usa_a_quantidade_e_o_fechamento_daquele_dia(
    sessao, cenario, com_extrato, publicando
):
    """Sábado, 14/02: 100 ações (o aumento é de março) a 40 (o fechamento de sexta)."""
    corpo = pedir(publicando, data="2026-02-14").get_json()

    assert corpo["data_de_referencia"] == "2026-02-14"
    (linha,) = corpo["posicoes"]
    assert linha["id"] == f"controle-renda-variavel:posicao:{com_extrato.id}"
    assert linha["instituicao"] == "genial"
    assert Decimal(linha["quantidade"]) == Decimal("100")
    assert Decimal(linha["preco"]) == Decimal("40")
    assert Decimal(linha["valor_a_mercado"]) == Decimal("4000.00")
    # A data do preço, e não a pedida: quem lê vê que é o fechamento de sexta.
    assert linha["preco_em"] == "2026-02-13"
    assert linha["situacao_do_preco"] == "fechamento"
    assert corpo["totais_por_moeda"] == [{"moeda": "BRL", "total": "4000.00", "linhas": 1}]


@banco
def test_o_aumento_entra_na_data_em_que_aconteceu(sessao, cenario, com_extrato, publicando):
    corpo = pedir(publicando, data="2026-03-31").get_json()

    (linha,) = corpo["posicoes"]
    assert Decimal(linha["quantidade"]) == Decimal("300")
    assert Decimal(linha["valor_a_mercado"]) == Decimal("13500.00")


@banco
def test_antes_da_abertura_a_posicao_nao_existia(sessao, cenario, com_extrato, publicando):
    corpo = pedir(publicando, data="2026-01-09").get_json()

    assert corpo["posicoes"] == []
    assert corpo["totais_por_moeda"] == []
    assert corpo["omitidas"]["sem_cotacao"] == 0


@banco
def test_fechamento_velho_nao_vira_valor_e_a_omissao_e_contada(
    sessao, cenario, com_extrato, publicando
):
    """Em 28/02 o último fechamento é de 13/02: quinze dias é buraco na série.

    Publicar a posição a 40 inventaria um valor; omiti-la calada faria o
    patrimônio encolher sem aviso. Ela sai, e a contagem diz que saiu.
    """
    corpo = pedir(publicando, data="2026-02-28").get_json()

    assert corpo["posicoes"] == []
    assert corpo["omitidas"]["sem_cotacao"] == 1


@banco
def test_posicao_encerrada_aparece_nas_datas_em_que_existia(sessao, cenario, publicando):
    """O extrato de uma posição encerrada sobrevive no arquivo, e é ele que a
    devolve ao passado. Sem ele, todo patrimônio antigo mediria só o que
    continuou na carteira."""
    sessao.add_all(
        [
            PositionLedgerArchive(
                owner_id=cenario["usuario"].id,
                occurred_on=dia,
                ticker_id=cenario["papel"].id,
                portfolio_id=cenario["real"].id,
                broker_id=cenario["corretora"].id,
                instrument="stock",
                source_position_id=987,
                resulting_signed_quantity=Decimal(quantidade),
            )
            for dia, quantidade in ((date(2025, 6, 2), "200"), (date(2025, 9, 1), "0"))
        ]
    )
    sessao.add_all(
        [
            _fechamento(cenario, date(2025, 7, 1), "30"),
            _fechamento(cenario, date(2025, 10, 1), "31"),
        ]
    )
    sessao.flush()

    julho = pedir(publicando, data="2025-07-01").get_json()
    outubro = pedir(publicando, data="2025-10-01").get_json()

    (linha,) = julho["posicoes"]
    assert linha["id"] == "controle-renda-variavel:posicao:987"
    assert Decimal(linha["valor_a_mercado"]) == Decimal("6000.00")
    assert outubro["posicoes"] == []


@banco
def test_posicao_vendida_no_passado_entra_com_valor_negativo(sessao, cenario, publicando):
    posicao = _posicao(cenario, cenario["real"], side=Side.SELL, quantity=Decimal("50"))
    sessao.add(posicao)
    sessao.flush()
    sessao.add_all(
        [
            _movimento(cenario, posicao, date(2026, 1, 10), PositionMovementKind.OPEN, "50", "50"),
            _fechamento(cenario, date(2026, 2, 13), "40"),
        ]
    )
    sessao.flush()

    (linha,) = pedir(publicando, data="2026-02-13").get_json()["posicoes"]

    assert Decimal(linha["quantidade"]) == Decimal("-50")
    assert Decimal(linha["valor_a_mercado"]) == Decimal("-2000.00")


@banco
def test_posicao_sem_extrato_conta_desde_o_cadastro(sessao, cenario, publicando):
    """Posição legada, anterior ao extrato: sem a abertura sintética ela sumiria
    de toda data passada, e o patrimônio encolheria sem aviso."""
    sessao.add(_posicao(cenario, cenario["real"], opened_on=date(2026, 1, 10)))
    sessao.add(_fechamento(cenario, date(2026, 2, 13), "40"))
    sessao.flush()

    antes = pedir(publicando, data="2026-01-09").get_json()
    depois = pedir(publicando, data="2026-02-13").get_json()

    assert antes["posicoes"] == []
    (linha,) = depois["posicoes"]
    assert Decimal(linha["valor_a_mercado"]) == Decimal("12000.00")


@banco
def test_simulada_fica_de_fora_tambem_no_passado(sessao, cenario, com_extrato, publicando):
    sessao.add(
        _posicao(
            cenario, cenario["simulada"], quantity=Decimal("1000"), opened_on=date(2026, 2, 1)
        )
    )
    sessao.flush()

    janeiro = pedir(publicando, data="2026-01-31").get_json()
    fevereiro = pedir(publicando, data="2026-02-14").get_json()

    assert janeiro["omitidas"]["simuladas"] == 0
    assert fevereiro["omitidas"]["simuladas"] == 1
    assert Decimal(fevereiro["totais_por_moeda"][0]["total"]) == Decimal("4000.00")


@banco
def test_proventos_de_depois_da_data_nao_entram(sessao, cenario, com_extrato, publicando):
    sessao.add_all(
        [
            Dividend(
                owner_id=cenario["usuario"].id,
                broker_id=cenario["corretora"].id,
                ticker_id=cenario["papel"].id,
                amount=Decimal("12.50"),
                payment_date=dia,
            )
            for dia in (date(2026, 2, 10), date(2026, 3, 10))
        ]
    )
    sessao.flush()

    corpo = pedir(publicando, data="2026-02-14").get_json()

    assert [provento["data"] for provento in corpo["proventos"]] == ["2026-02-10"]
    assert corpo["proventos_desde"] == "2025-02-14"


@banco
def test_data_futura_e_recusada(sessao, cenario, publicando):
    assert pedir(publicando, data="2999-01-01").status_code == 400


class _NoiteDeSetembro(datetime):
    """17/09/2026 às 22h30 em Brasília, que em UTC já é 18/09."""

    @classmethod
    def now(cls, tz=None):
        instante = datetime(2026, 9, 18, 1, 30, tzinfo=UTC)
        return instante.astimezone(tz) if tz else instante.replace(tzinfo=None)


@banco
def test_hoje_e_o_dia_em_brasilia(sessao, cenario, publicando, monkeypatch):
    """Às 22h30 de 17/09, "hoje" é 17/09.

    Em UTC já seria 18/09: o dia 17 viraria passado, sairia do fechamento em
    vez da cotação ao vivo, e o dia 18 seria aceito como se já tivesse
    acontecido.
    """
    import app.routes.patrimonio as rota

    monkeypatch.setattr(rota, "datetime", _NoiteDeSetembro)
    sessao.add(_posicao(cenario, cenario["real"]))
    sessao.flush()

    hoje = pedir(publicando, data="2026-09-17").get_json()
    amanha = pedir(publicando, data="2026-09-18")

    assert hoje["data_de_referencia"] == "2026-09-17"
    (linha,) = hoje["posicoes"]
    assert linha["situacao_do_preco"] == "online"
    assert Decimal(linha["valor_a_mercado"]) == Decimal("15630.00")
    assert amanha.status_code == 400


@banco
def test_a_resposta_nao_pode_ser_guardada_por_intermediario(sessao, cenario, publicando):
    assert pedir(publicando).headers["Cache-Control"] == "no-store"


# --- O endereço leva à tela de origem --------------------------------------


@banco
def test_a_posicao_leva_a_propria_pagina_analitica(sessao, cenario, publicando):
    """O caminho é relativo: o endereço público é de quem consome."""
    posicao = _posicao(cenario, cenario["real"])
    sessao.add(posicao)
    sessao.flush()

    (linha,) = pedir(publicando).get_json()["posicoes"]

    assert linha["endereco"] == f"/positions/{posicao.id}"


@banco
def test_no_passado_a_posicao_viva_leva_a_mesma_pagina(sessao, cenario, com_extrato, publicando):
    (linha,) = pedir(publicando, data="2026-02-14").get_json()["posicoes"]

    assert linha["endereco"] == f"/positions/{com_extrato.id}"


@banco
def test_posicao_encerrada_nao_tem_para_onde_levar(sessao, cenario, publicando):
    """Ela só existe no arquivo; um link para a carteira abriria sem ela."""
    sessao.add(
        PositionLedgerArchive(
            owner_id=cenario["usuario"].id,
            occurred_on=date(2025, 6, 2),
            ticker_id=cenario["papel"].id,
            portfolio_id=cenario["real"].id,
            broker_id=cenario["corretora"].id,
            instrument="stock",
            source_position_id=987,
            resulting_signed_quantity=Decimal("200"),
        )
    )
    sessao.add(_fechamento(cenario, date(2025, 7, 1), "30"))
    sessao.flush()

    (linha,) = pedir(publicando, data="2025-07-01").get_json()["posicoes"]

    assert linha["endereco"] is None


@banco
def test_o_endereco_publicado_abre_a_pagina_da_posicao(
    sessao, cenario, com_extrato, app_com_banco, monkeypatch
):
    """O caminho publicado (conferido acima) abre a rota individual protegida."""
    usuario = cenario["usuario"]
    usuario.role = ROLE_ADMIN
    usuario.is_active_user = True
    usuario.must_change_password = False
    monkeypatch.setattr(login_manager, "_user_callback", lambda _user_id: usuario)
    navegador = app_com_banco.test_client()
    with navegador.session_transaction() as sessao_http:
        sessao_http["_user_id"] = str(usuario.id)
        sessao_http["_fresh"] = True

    resposta = navegador.get(f"/positions/{com_extrato.id}")

    assert resposta.status_code == 200, resposta.headers.get("Location")
    assert "Métricas da posição" in resposta.get_data(as_text=True)
