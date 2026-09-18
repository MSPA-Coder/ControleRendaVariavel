"""O resumo que este sistema publica para o consolidador de patrimônio.

É a metade gêmea da rota que o Controle Bancário já publica: mesmo caminho,
mesmo envelope, mesmo jeito de autenticar. Ele publica o **caixa**; este publica
o **investimento**. Quem soma é um terceiro aplicativo, só de leitura, que não
toca no banco de nenhum dos dois -- ler o banco alheio acoplaria os schemas e
quebraria a cada migration.

O VOCABULÁRIO COMUM

Titular e instituição são identificados pelo **nome normalizado**, não pelo id
do banco: "Genial" é a corretora 1 aqui e outra coisa lá, mas é a mesma
corretora para quem lê. `identidade()` tem de produzir exatamente o mesmo
resultado dos dois lados -- se um normalizar "Itaú" e o outro "itau", viram dois
titulares e o patrimônio aparece **dobrado**. Os dois repositórios têm o mesmo
teste, com os mesmos casos, justamente para isso não derivar em silêncio.

Todo valor viaja como **texto**: `float` não representa 0,10 e quem consolida
somaria centavos que nunca existiram.

TRÊS COISAS QUE ELE OMITE, E DIZ QUE OMITIU

1. **Carteira simulada.** Metade das posições desta base está numa, e somá-la ao
   patrimônio o infla com dinheiro que não existe -- sem que o número deixe de
   parecer plausível. A tela de Posições já trata "Todas" como as carteiras
   reais; aqui vale a mesma regra;
2. **Opções.** Elas têm valor e ficarão de fora até serem publicadas com o mesmo
   cuidado das ações. Enquanto isso, a contagem aparece no envelope;
3. **Posição sem cotação.** Sem preço não há valor a mercado, e inventar um
   seria pior do que faltar.

As três contagens vão em `omitidas`. Omissão contada é omissão visível; omissão
silenciosa é um patrimônio errado com cara de completo.

O ENDEREÇO É DAQUI

Cada posição leva `endereco`: o caminho, relativo à raiz deste sistema, da sua
própria página analítica. Quem consome junta o caminho ao endereço público que
já conhece; o id continua opaco. Posição já encerrada não tem tela, e vai com
`endereco` nulo. A rota individual confere a posse no servidor, então um link
de uma posição de outro dono não revela a carteira nem o ativo.

HOJE E UMA DATA PASSADA SÃO DUAS PERGUNTAS

**Hoje** é a carteira que está aberta, pela cotação ao vivo do coletor.

**Uma data passada** é outra conta, e só pode ser respondida com o que era
verdade NAQUELE dia:

- a quantidade vem do extrato (`PositionMovement`, mais o arquivo das posições
  já encerradas), pela mesma linha do tempo que o TWR usa -- nunca a
  quantidade de hoje aplicada a março;
- o preço é o **fechamento** daquele dia em `quote_history`, ou o último antes
  dele, e a data do preço viaja em `preco_em`. Fechamento com mais de sete dias
  não vale, e a posição conta como sem cotação.

Aplicar a cotação de hoje a uma carteira de março responderia um número que
nunca existiu; por isso, antes desta rota saber reconstruir a data, ela
recusava a pergunta.

"Hoje" é o dia em Brasília. Em UTC, das 21h à meia-noite a rota já estaria no
dia seguinte, e pedir a data do dia seria recusado como data futura.

Dois limites herdados do extrato, os mesmos do relatório de performance:

- `opened_on` de uma posição antiga costuma ser a data em que ela foi
  **cadastrada**, e não a da compra. Antes dela, a posição não aparece;
- o arquivo das posições encerradas não guarda o multiplicador da cotação.
  Elas entram com multiplicador 1, que é o valor de todas as posições desta
  base.

E um limite herdado da série de cotações: a linha de `quote_history` de um dia é
a última observação daquele dia, e se a coleta parou no meio do pregão ela é um
preço **parcial**, não o fechamento. Este módulo não tem como distinguir os dois
-- fazê-lo exigiria saber o horário de fechamento de cada bolsa, com feriado,
leilão e fechamento antecipado, e erraria calado. O mesmo parcial contamina o
TWR e o risco, então o conserto é de lá: apagar o dia e reimportar. Por isso
`preco_em` leva o DIA do fechamento, e não o instante: a barra diária do Yahoo
vem carimbada na abertura do pregão, e publicá-la como instante da observação
seria pior do que publicar o dia.
"""

from __future__ import annotations

import hmac
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from flask import abort, current_app, jsonify, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app import db
from app.core.domain import MARKET_TIMEZONE
from app.models import (
    Broker,
    Dividend,
    OptionPosition,
    OptionPositionMovement,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    QuoteHistory,
    Side,
    Ticker,
)
from app.positions.holdings_history import (
    CLOSING_PRICE_VALIDITY_DAYS,
    HoldingEvent,
    QuantityTimeline,
    closing_price_on,
)
from app.positions.portfolio import effective_position_quote
from app.routes import bp

CONTRATO = "patrimonio/v1"
SISTEMA = "controle-renda-variavel"

#: Janela dos proventos publicados. Eles não entram no patrimônio de hoje (já
#: foram recebidos e viraram caixa, que é do outro sistema); vão no envelope
#: porque o consolidador mostra renda do período. Sem janela, a lista cresceria
#: para sempre.
JANELA_DE_PROVENTOS_EM_DIAS = 365


def identidade(nome: str) -> str:
    """O identificador comum de um titular ou de uma instituição.

    Cópia deliberada de `core.patrimonio.identidade`, do Controle Bancário. Ela
    não mora no `sharedauth` ainda porque nasceu com um consumidor só; agora tem
    dois, e é candidata natural a subir para lá. Enquanto não sobe, o que impede
    as duas de derivarem é o teste com os mesmos casos nos dois repositórios.
    """
    decomposto = unicodedata.normalize("NFKD", nome or "")
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def token_valido_apresentado() -> bool:
    """O pedido traz o token certo? Falso quando nenhum token está configurado.

    Usada também pelo limitador de taxa (`app/__init__.py`), que isenta quem
    tem o token: comparar em tempo constante custa quase nada, e é a mesma
    comparação da rota.
    """
    configurado = str(current_app.config.get("PATRIMONIO_TOKEN") or "")
    if not configurado:
        return False
    apresentado = request.headers.get("Authorization", "")
    return hmac.compare_digest(apresentado, f"Bearer {configurado}")


def _exigir_token() -> None:
    """Mesmo contrato do agente do coletor, e pelas mesmas razões.

    503 quando ninguém configurou a integração aqui; 401 quando o token está
    errado. A diferença importa: dizer 401 a quem nunca recebeu token mandaria o
    operador procurar por horas um segredo que nunca foi concedido.
    """
    if not current_app.config.get("PATRIMONIO_TOKEN"):
        abort(503, "Publicação de patrimônio não configurada.")
    if not token_valido_apresentado():
        abort(401, "Não autorizado.")


def _titular() -> str:
    """De quem é o dinheiro que este sistema registra.

    Ele não sabe sozinho: `Position.owner_id` aponta para `users.id`, que é o
    usuário do aplicativo -- quem opera --, e não a pessoa dona do dinheiro. No
    Controle Bancário titular é outra entidade (`AccountOwner`: Mariano,
    Cláudia, Esther), e publicar o nome de usuário como se fosse titular
    funcionaria só enquanto os dois coincidissem.

    Por isso é configuração obrigatória, e não palpite: sem ela a rota não
    publica. No dia em que houver dinheiro de duas pessoas aqui, uma variável só
    deixa de bastar e isto vira um mapeamento por carteira.
    """
    nome = str(current_app.config.get("PATRIMONIO_TITULAR") or "").strip()
    if not nome:
        abort(
            503,
            "Publicação de patrimônio sem titular: defina PATRIMONIO_TITULAR com a "
            "pessoa dona do dinheiro registrado neste sistema.",
        )
    return nome


def _posicoes_reais() -> list[Position]:
    """Todas as posições de carteiras reais, de todos os donos.

    Sem escopo por usuário, e isso é deliberado -- igual ao outro publicador: um
    resumo filtrado produziria um patrimônio consolidado que esconde posições
    sem avisar, que é pior do que não responder.
    """
    consulta = (
        select(Position)
        .join(Position.portfolio_ref)
        .options(
            joinedload(Position.quote),
            joinedload(Position.broker_ref),
            joinedload(Position.ticker_ref),
            joinedload(Position.portfolio_ref),
        )
        .order_by(Position.id)
    )
    return list(db.session.scalars(consulta).unique().all())


def _proventos(desde: date, ate: date) -> list[Dividend]:
    consulta = (
        select(Dividend)
        .where(Dividend.payment_date >= desde, Dividend.payment_date <= ate)
        .options(joinedload(Dividend.broker_ref), joinedload(Dividend.ticker_ref))
        .order_by(Dividend.payment_date, Dividend.id)
    )
    return list(db.session.scalars(consulta).unique().all())


CENTAVO = Decimal("0.01")


def _dinheiro(valor: Decimal) -> str:
    """Dinheiro tem duas casas, e a multiplicação não sabe disso.

    `quantidade × preço` com duas colunas `Numeric(24,8)` produz uma escala de
    vinte e poucas casas -- `194796.0000000000000000000000`. O número está
    certo e a apresentação convida ao erro: quem consolidar pode arredondar
    diferente do que este sistema arredondaria, e os dois passam a discordar.
    """
    return format(valor.quantize(CENTAVO, rounding=ROUND_HALF_UP), "f")


def _numero(valor: Decimal) -> str:
    """Quantidade e preço, sem zeros à direita e sem notação científica.

    `Decimal.normalize()` resolveria os zeros e criaria o outro problema:
    `Decimal("300").normalize()` vira `3E+2`, que nenhum consumidor espera.
    """
    texto = format(valor, "f")
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


@dataclass
class _Foto:
    """O que a rota vai publicar, montado posição a posição.

    Hoje e uma data passada chegam aqui pelo mesmo caminho: o que muda entre
    eles é de onde vêm a quantidade e o preço, não como a linha é escrita. Uma
    forma só de escrever a linha é o que garante que o consolidador leia as
    duas perguntas do mesmo jeito.
    """

    titular: str
    instituicoes: dict[str, dict] = field(default_factory=dict)
    linhas: list[dict] = field(default_factory=list)
    totais: dict[str, dict] = field(default_factory=dict)

    def instituicao(self, nome: str) -> str:
        chave = identidade(nome)
        self.instituicoes.setdefault(chave, {"id": chave, "nome": nome, "tipo": "Corretora"})
        return chave

    def posicao(
        self,
        *,
        posicao_id: int,
        corretora: str,
        instrumento: str,
        mercado: str,
        moeda: str,
        quantidade: Decimal,
        preco: Decimal,
        preco_em: str,
        situacao_do_preco: str,
        viva: bool = True,
    ) -> None:
        """`quantidade` já vem com o sinal do lado, e `preco` já multiplicado.

        `viva` diz se a posição ainda existe na carteira, e com isso se há uma
        tela para onde levar quem clica nela.
        """
        valor = quantidade * preco
        self.linhas.append(
            {
                "id": f"{SISTEMA}:posicao:{posicao_id}",
                "titular": self.titular,
                "instituicao": self.instituicao(corretora),
                "instrumento": instrumento,
                "classe": "acao",
                "mercado": mercado,
                "quantidade": _numero(quantidade),
                "moeda": moeda,
                "preco": _numero(preco),
                "valor_a_mercado": _dinheiro(valor),
                "preco_em": preco_em,
                "fonte_do_preco": SISTEMA,
                "situacao_do_preco": situacao_do_preco,
                "endereco": (
                    url_for("portfolio.position_detail", position_id=posicao_id) if viva else None
                ),
            }
        )
        bloco = self.totais.setdefault(moeda, {"moeda": moeda, "total": Decimal("0"), "linhas": 0})
        bloco["total"] += valor
        bloco["linhas"] += 1


def _data_pedida(hoje: date) -> date:
    pedida = (request.args.get("data") or "").strip()
    if not pedida:
        return hoje
    try:
        referencia = date.fromisoformat(pedida)
    except ValueError:
        abort(400, "Data inválida: use AAAA-MM-DD.")
    if referencia > hoje:
        abort(400, "Data futura: não há posição nem fechamento para ela.")
    return referencia


def _fotografar_hoje(foto: _Foto) -> dict[str, int]:
    """A carteira aberta agora, pela cotação ao vivo do coletor."""
    simuladas = 0
    sem_cotacao = 0
    for posicao in _posicoes_reais():
        if posicao.simulated:
            simuladas += 1
            continue
        if posicao.quote is None:
            sem_cotacao += 1
            continue
        preco, observado_em = effective_position_quote(posicao)
        direcao = Decimal("1") if posicao.side.value == "C" else Decimal("-1")
        foto.posicao(
            posicao_id=posicao.id,
            corretora=posicao.broker,
            instrumento=posicao.ticker,
            mercado=posicao.ticker_ref.market.value,
            moeda=posicao.currency,
            quantidade=posicao.quantity * direcao,
            preco=preco * posicao.quote_multiplier,
            preco_em=observado_em.isoformat(),
            # O estado que o próprio coletor gravou. A idade do preço quem
            # diz é `preco_em`, e é ela que o consumidor deve usar para
            # julgar: o limiar de "velho" desta casa é de 30 segundos,
            # pensado para uma tela ao vivo durante o pregão, e aplicá-lo a
            # uma foto diária marcaria como velho todo preço fora do horário
            # de mercado -- um alarme que toca sempre não avisa nada.
            situacao_do_preco=posicao.quote.source_status,
        )
    return {"simuladas": simuladas, "opcoes": _quantas_opcoes(), "sem_cotacao": sem_cotacao}


@dataclass(frozen=True, slots=True)
class _Origem:
    """O que o extrato não carrega e a linha publicada precisa."""

    corretora_id: int
    multiplicador: Decimal
    viva: bool


def _sinal(lado: Side) -> Decimal:
    return Decimal("1") if lado == Side.BUY else Decimal("-1")


def _extrato_das_acoes() -> tuple[list[HoldingEvent], dict[tuple[str, int], _Origem]]:
    """Todo o extrato de ações das carteiras reais, de todos os donos.

    É a mesma leitura de `app.routes.helpers.position_movement_events`, que
    alimenta o TWR, com três diferenças: não tem escopo de usuário (pelo mesmo
    motivo de `_posicoes_reais`), carrega a corretora e o multiplicador de cada
    posição, e dá a uma posição viva SEM extrato uma abertura sintética em
    `opened_on` com a quantidade atual. Sem ela, essa posição sumiria de toda
    data passada: um patrimônio menor, calado. Com ela, a posição conta desde a
    data em que foi cadastrada, que é tudo o que o banco sabe dela.
    """
    data_do_evento = func.coalesce(PositionMovement.occurred_on, Position.opened_on)
    quantidade_do_evento = func.coalesce(PositionMovement.resulting_quantity, Position.quantity)
    vivas = (
        select(
            data_do_evento,
            Position.id,
            Position.ticker_id,
            Position.side,
            quantidade_do_evento,
            Position.broker_id,
            Position.quote_multiplier,
        )
        .select_from(Position)
        .join(Position.portfolio_ref)
        .outerjoin(PositionMovement, PositionMovement.position_id == Position.id)
        .where(Portfolio.simulated.is_(False))
        .order_by(data_do_evento, PositionMovement.id)
    )
    encerradas = (
        select(
            PositionLedgerArchive.occurred_on,
            PositionLedgerArchive.source_position_id,
            PositionLedgerArchive.ticker_id,
            PositionLedgerArchive.resulting_signed_quantity,
            PositionLedgerArchive.broker_id,
        )
        .join(Portfolio, Portfolio.id == PositionLedgerArchive.portfolio_id)
        .where(Portfolio.simulated.is_(False), PositionLedgerArchive.instrument == "stock")
        .order_by(PositionLedgerArchive.occurred_on, PositionLedgerArchive.id)
    )

    eventos: list[HoldingEvent] = []
    origens: dict[tuple[str, int], _Origem] = {}
    for dia, posicao_id, ticker_id, lado, quantidade, corretora_id, multiplicador in (
        db.session.execute(vivas)
    ):
        chave = ("stock", posicao_id)
        eventos.append(HoldingEvent(dia, ticker_id, _sinal(lado) * quantidade, chave))
        origens[chave] = _Origem(corretora_id, multiplicador, viva=True)
    for dia, posicao_id, ticker_id, quantidade, corretora_id in db.session.execute(encerradas):
        chave = ("stock", posicao_id)
        # O sinal já foi aplicado quando o arquivo foi gravado. O multiplicador
        # não foi guardado: veja o docstring do módulo.
        eventos.append(HoldingEvent(dia, ticker_id, quantidade, chave))
        # Viva é quem ainda está na carteira; o arquivo não tira isso dela.
        viva = chave in origens and origens[chave].viva
        origens[chave] = _Origem(corretora_id, Decimal("1"), viva=viva)
    return eventos, origens


def _opcoes_detidas_em(referencia: date) -> int:
    """Quantas opções de carteira real estavam abertas na data.

    Opções continuam fora do resumo; a contagem é o que torna a omissão
    visível, e numa data passada ela tem de ser a daquela data.
    """
    data_do_evento = func.coalesce(OptionPositionMovement.occurred_on, OptionPosition.opened_on)
    quantidade_do_evento = func.coalesce(
        OptionPositionMovement.resulting_quantity, OptionPosition.quantity
    )
    vivas = (
        select(data_do_evento, OptionPosition.id, quantidade_do_evento)
        .select_from(OptionPosition)
        .join(OptionPosition.portfolio_ref)
        .outerjoin(
            OptionPositionMovement,
            OptionPositionMovement.option_position_id == OptionPosition.id,
        )
        .where(Portfolio.simulated.is_(False))
        .order_by(data_do_evento, OptionPositionMovement.id)
    )
    encerradas = (
        select(
            PositionLedgerArchive.occurred_on,
            PositionLedgerArchive.source_position_id,
            PositionLedgerArchive.resulting_signed_quantity,
        )
        .join(Portfolio, Portfolio.id == PositionLedgerArchive.portfolio_id)
        .where(Portfolio.simulated.is_(False), PositionLedgerArchive.instrument == "option")
        .order_by(PositionLedgerArchive.occurred_on, PositionLedgerArchive.id)
    )
    eventos = [
        HoldingEvent(dia, 0, quantidade, ("option", posicao_id))
        for dia, posicao_id, quantidade in (
            *db.session.execute(vivas),
            *db.session.execute(encerradas),
        )
    ]
    quantidades = QuantityTimeline(eventos).quantities_at(referencia)
    return sum(1 for quantidade in quantidades.values() if quantidade != 0)


def _simuladas_em(referencia: date) -> int:
    """Posições simuladas abertas até a data.

    Carteira simulada não guarda extrato, então isto conta as que existem hoje
    e já existiam na data. Uma simulada encerrada antes de hoje não deixa
    rastro. É uma contagem aproximada, e só serve para mostrar que houve
    omissão: nenhuma simulada entra no valor, em data nenhuma.
    """
    return int(
        db.session.scalar(
            select(func.count())
            .select_from(Position)
            .join(Position.portfolio_ref)
            .where(Portfolio.simulated.is_(True), Position.opened_on <= referencia)
        )
        or 0
    )


def _fechamentos(
    ticker_ids: list[int], referencia: date
) -> dict[int, list[tuple[date, Decimal]]]:
    """Os fechamentos que ainda valem para a data, por ticker, em ordem.

    A janela é a própria validade do fechamento: o que é mais velho não seria
    usado de qualquer forma, e ler a série inteira a cada data pedida tornaria
    cara a reconstrução de anos de história, uma data por vez.
    """
    if not ticker_ids:
        return {}
    consulta = (
        select(QuoteHistory.ticker_id, QuoteHistory.recorded_date, QuoteHistory.price)
        .where(
            QuoteHistory.ticker_id.in_(ticker_ids),
            QuoteHistory.recorded_date <= referencia,
            QuoteHistory.recorded_date
            >= referencia - timedelta(days=CLOSING_PRICE_VALIDITY_DAYS),
        )
        .order_by(QuoteHistory.ticker_id, QuoteHistory.recorded_date)
    )
    series: dict[int, list[tuple[date, Decimal]]] = {}
    for ticker_id, dia, preco in db.session.execute(consulta):
        series.setdefault(ticker_id, []).append((dia, preco))
    return series


def _fotografar_passado(foto: _Foto, referencia: date) -> dict[str, int]:
    """A carteira como estava no fim de `referencia`, a preço de fechamento."""
    eventos, origens = _extrato_das_acoes()
    linha_do_tempo = QuantityTimeline(eventos)
    abertas = {
        chave: quantidade
        for chave, quantidade in linha_do_tempo.quantities_at(referencia).items()
        if quantidade != 0
    }

    ticker_ids = sorted({linha_do_tempo.ticker_of(chave) for chave in abertas})
    tickers = {
        ticker.id: ticker
        for ticker in db.session.scalars(select(Ticker).where(Ticker.id.in_(ticker_ids)))
    }
    corretora_ids = sorted({origens[chave].corretora_id for chave in abertas})
    corretoras = dict(
        db.session.execute(select(Broker.id, Broker.name).where(Broker.id.in_(corretora_ids)))
        .tuples()
        .all()
    )
    fechamentos = _fechamentos(ticker_ids, referencia)

    sem_cotacao = 0
    for chave in sorted(abertas, key=lambda item: item[1]):
        ticker = tickers[linha_do_tempo.ticker_of(chave)]
        fechamento = closing_price_on(fechamentos.get(ticker.id, []), referencia)
        if fechamento is None:
            sem_cotacao += 1
            continue
        dia_do_preco, preco = fechamento
        origem = origens[chave]
        foto.posicao(
            posicao_id=chave[1],
            corretora=corretoras[origem.corretora_id],
            instrumento=ticker.symbol,
            mercado=ticker.market.value,
            moeda=ticker.currency,
            quantidade=abertas[chave],
            preco=preco * origem.multiplicador,
            # A data do PREÇO, não a pedida: sábado vale o fechamento de sexta,
            # e quem lê precisa poder ver isso.
            preco_em=dia_do_preco.isoformat(),
            situacao_do_preco="fechamento",
            viva=origem.viva,
        )
    return {
        "simuladas": _simuladas_em(referencia),
        "opcoes": _opcoes_detidas_em(referencia),
        "sem_cotacao": sem_cotacao,
    }


@bp.get("/patrimonio/v1/resumo")
def patrimonio_resumo():
    _exigir_token()
    titular_nome = _titular()
    foto = _Foto(titular=identidade(titular_nome))

    hoje = datetime.now(MARKET_TIMEZONE).date()
    referencia = _data_pedida(hoje)
    if referencia == hoje:
        omitidas = _fotografar_hoje(foto)
    else:
        omitidas = _fotografar_passado(foto, referencia)

    desde = referencia - timedelta(days=JANELA_DE_PROVENTOS_EM_DIAS)
    proventos = []
    for provento in _proventos(desde, referencia):
        proventos.append(
            {
                "id": f"{SISTEMA}:provento:{provento.id}",
                "titular": foto.titular,
                "instituicao": foto.instituicao(provento.broker),
                "instrumento": provento.ticker_ref.symbol,
                "tipo": provento.kind.value,
                "moeda": provento.ticker_ref.currency,
                "valor": _dinheiro(provento.amount),
                "data": provento.payment_date.isoformat(),
            }
        )

    resposta = jsonify(
        {
            "contrato": CONTRATO,
            "sistema": SISTEMA,
            "papel": "investimento",
            "gerado_em": datetime.now(UTC).isoformat(),
            "data_de_referencia": referencia.isoformat(),
            "titulares": [{"id": foto.titular, "nome": titular_nome}],
            "instituicoes": [foto.instituicoes[chave] for chave in sorted(foto.instituicoes)],
            # Conta é do outro publicador: o caixa das corretoras vive no
            # Controle Bancário desde a decisão de 16/09/2026.
            "contas": [],
            "totais_por_moeda": [
                {"moeda": moeda, "total": _dinheiro(dados["total"]), "linhas": dados["linhas"]}
                for moeda, dados in sorted(foto.totais.items())
            ],
            "posicoes": foto.linhas,
            "proventos": proventos,
            "proventos_desde": desde.isoformat(),
            "ativos_alternativos": [],
            "omitidas": omitidas,
        }
    )
    # A foto carrega a carteira inteira: nenhum intermediário deve guardá-la.
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def _quantas_opcoes() -> int:
    return int(
        db.session.scalar(
            select(db.func.count())
            .select_from(OptionPosition)
            .join(OptionPosition.portfolio_ref)
        )
        or 0
    )
