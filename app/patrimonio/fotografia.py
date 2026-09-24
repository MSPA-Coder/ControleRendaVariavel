"""A fotografia do patrimônio publicada ao consolidador (NetWorth).

Monta as linhas de posição de hoje (cotação ao vivo) ou de uma data passada
(fechamentos), e a identidade dos titulares e instituições. Quem decide o que
foi pedido -- token, titular, data -- é a rota `app.routes.patrimonio`; este
módulo só fotografa. Saiu de lá em 24/09/2026 porque `app.patrimonio.dashboard`
precisava dele e o importava da camada web por import tardio.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from flask import url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app import db
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

SISTEMA = "controle-renda-variavel"


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


def _posicoes_reais(owner_id: int) -> list[Position]:
    """Posições das carteiras do dono financeiro publicado."""
    consulta = (
        select(Position)
        .join(Position.portfolio_ref)
        .where(Position.owner_id == owner_id)
        .options(
            joinedload(Position.quote),
            joinedload(Position.broker_ref),
            joinedload(Position.ticker_ref),
            joinedload(Position.portfolio_ref),
        )
        .order_by(Position.id)
    )
    return list(db.session.scalars(consulta).unique().all())


def _proventos(desde: date, ate: date, owner_id: int) -> list[Dividend]:
    consulta = (
        select(Dividend)
        .where(
            Dividend.owner_id == owner_id,
            Dividend.payment_date >= desde,
            Dividend.payment_date <= ate,
        )
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


def _fotografar_hoje(foto: _Foto, owner_id: int) -> dict[str, int]:
    """A carteira aberta agora, pela cotação ao vivo do coletor."""
    simuladas = 0
    sem_cotacao = 0
    for posicao in _posicoes_reais(owner_id):
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
            preco=preco,
            preco_em=observado_em.isoformat(),
            # O estado que o próprio coletor gravou. A idade do preço quem
            # diz é `preco_em`, e é ela que o consumidor deve usar para
            # julgar: o limiar de "velho" desta casa é de 30 segundos,
            # pensado para uma tela ao vivo durante o pregão, e aplicá-lo a
            # uma foto diária marcaria como velho todo preço fora do horário
            # de mercado -- um alarme que toca sempre não avisa nada.
            situacao_do_preco=posicao.quote.source_status,
        )
    return {
        "simuladas": simuladas,
        "opcoes": _quantas_opcoes(owner_id),
        "sem_cotacao": sem_cotacao,
    }


@dataclass(frozen=True, slots=True)
class _Origem:
    """O que o extrato não carrega e a linha publicada precisa."""

    corretora_id: int
    viva: bool


def _sinal(lado: Side) -> Decimal:
    return Decimal("1") if lado == Side.BUY else Decimal("-1")


def _extrato_das_acoes(
    referencia: date, owner_id: int
) -> tuple[list[HoldingEvent], dict[tuple[str, int], _Origem]]:
    """Extrato de ações das carteiras reais do dono publicado.

    É a mesma leitura de `app.routes.helpers.position_movement_events`, que
    alimenta o TWR, com três diferenças: não tem escopo de usuário (pelo mesmo
    motivo de `_posicoes_reais`), carrega a corretora de cada posição, e dá a uma posição viva SEM extrato uma abertura sintética em
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
        )
        .select_from(Position)
        .join(Position.portfolio_ref)
        .outerjoin(PositionMovement, PositionMovement.position_id == Position.id)
        .where(
            Position.owner_id == owner_id,
            Portfolio.simulated.is_(False),
            data_do_evento <= referencia,
        )
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
        .where(
            PositionLedgerArchive.owner_id == owner_id,
            Portfolio.simulated.is_(False),
            PositionLedgerArchive.instrument == "stock",
            PositionLedgerArchive.occurred_on <= referencia,
        )
        .order_by(PositionLedgerArchive.occurred_on, PositionLedgerArchive.id)
    )

    eventos: list[HoldingEvent] = []
    origens: dict[tuple[str, int], _Origem] = {}
    for dia, posicao_id, ticker_id, lado, quantidade, corretora_id in (
        db.session.execute(vivas)
    ):
        chave = ("stock", posicao_id)
        eventos.append(HoldingEvent(dia, ticker_id, _sinal(lado) * quantidade, chave))
        origens[chave] = _Origem(corretora_id, viva=True)
    for dia, posicao_id, ticker_id, quantidade, corretora_id in db.session.execute(encerradas):
        chave = ("stock", posicao_id)
        # O sinal já foi aplicado quando o arquivo foi gravado.
        eventos.append(HoldingEvent(dia, ticker_id, quantidade, chave))
        # Viva é quem ainda está na carteira; o arquivo não tira isso dela.
        viva = chave in origens and origens[chave].viva
        origens[chave] = _Origem(corretora_id, viva=viva)
    return eventos, origens


def _opcoes_detidas_em(referencia: date, owner_id: int) -> int:
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
        .where(
            OptionPosition.owner_id == owner_id,
            Portfolio.simulated.is_(False),
            data_do_evento <= referencia,
        )
        .order_by(data_do_evento, OptionPositionMovement.id)
    )
    encerradas = (
        select(
            PositionLedgerArchive.occurred_on,
            PositionLedgerArchive.source_position_id,
            PositionLedgerArchive.resulting_signed_quantity,
        )
        .join(Portfolio, Portfolio.id == PositionLedgerArchive.portfolio_id)
        .where(
            PositionLedgerArchive.owner_id == owner_id,
            Portfolio.simulated.is_(False),
            PositionLedgerArchive.instrument == "option",
            PositionLedgerArchive.occurred_on <= referencia,
        )
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


def _simuladas_em(referencia: date, owner_id: int) -> int:
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
            .where(
                Position.owner_id == owner_id,
                Portfolio.simulated.is_(True),
                Position.opened_on <= referencia,
            )
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


def _fotografar_passado(foto: _Foto, referencia: date, owner_id: int) -> dict[str, int]:
    """A carteira como estava no fim de `referencia`, a preço de fechamento."""
    eventos, origens = _extrato_das_acoes(referencia, owner_id)
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
            preco=preco,
            # A data do PREÇO, não a pedida: sábado vale o fechamento de sexta,
            # e quem lê precisa poder ver isso.
            preco_em=dia_do_preco.isoformat(),
            situacao_do_preco="fechamento",
            viva=origem.viva,
        )
    return {
        "simuladas": _simuladas_em(referencia, owner_id),
        "opcoes": _opcoes_detidas_em(referencia, owner_id),
        "sem_cotacao": sem_cotacao,
    }


def _quantas_opcoes(owner_id: int) -> int:
    return int(
        db.session.scalar(
            select(db.func.count())
            .select_from(OptionPosition)
            .join(OptionPosition.portfolio_ref)
            .where(OptionPosition.owner_id == owner_id)
        )
        or 0
    )
