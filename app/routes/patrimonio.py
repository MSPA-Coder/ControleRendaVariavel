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

Um limite herdado do extrato, o mesmo do relatório de performance:
`opened_on` de uma posição antiga costuma ser a data em que ela foi
**cadastrada**, e não a da compra. Antes dela, a posição não aparece.

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

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from flask import abort, current_app, jsonify, request, url_for
from sqlalchemy import func, select, text
from sqlalchemy.orm import joinedload

from app import db
from app.core.domain import MARKET_TIMEZONE
from app.models import (
    Broker,
    Dividend,
    OptionContract,
    OptionPosition,
    OptionPositionMovement,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    QuoteHistory,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
)
from app.patrimonio import queries
from app.positions.holdings_history import (
    CLOSING_PRICE_VALIDITY_DAYS,
    HoldingEvent,
    QuantityTimeline,
    closing_price_on,
)
from app.positions.portfolio import effective_position_quote
from app.routes import bp

CONTRATO = "patrimonio/v1"
CONTRATO_V3 = "patrimonio/v3"
SISTEMA = "controle-renda-variavel"
V3_PAGE_SIZE = 50
V3_MAX_PAGE_SIZE = 100

#: Janela dos proventos publicados. Eles não entram no patrimônio de hoje (já
#: foram recebidos e viraram caixa, que é do outro sistema); vão no envelope
#: porque o consolidador mostra renda do período. Sem janela, a lista cresceria
#: para sempre.
JANELA_DE_PROVENTOS_EM_DIAS = 365


def _id_v3(recurso: str, valor: int) -> str:
    """ID estável que não expõe a chave primária operacional."""
    material = f"{SISTEMA}:{recurso}:{valor}".encode()
    digest = hashlib.sha256(material).hexdigest()[:24]
    return f"{SISTEMA}:{recurso}:{digest}"


def _id_v3_material(recurso: str, material: str) -> str:
    """ID opaco para recursos que não têm uma única chave operacional.

    Séries e eventos podem ser compostos por várias linhas do domínio. O
    material é montado pelo publicador, nunca aceito do chamador, e o digest
    evita expor a combinação de IDs internos no contrato HTTP.
    """
    digest = hashlib.sha256(f"{SISTEMA}:{recurso}:{material}".encode()).hexdigest()[:24]
    return f"{SISTEMA}:{recurso}:{digest}"


def _paginacao_v3() -> tuple[int, int]:
    try:
        pagina = int(request.args.get("page", request.args.get("pagina", "1")))
        tamanho = int(request.args.get("page_size", request.args.get("tamanho", str(V3_PAGE_SIZE))))
    except (TypeError, ValueError):
        abort(400, "page e page_size devem ser inteiros positivos")
    if pagina < 1 or tamanho < 1 or tamanho > V3_MAX_PAGE_SIZE:
        abort(400, f"page deve ser positivo e page_size deve estar entre 1 e {V3_MAX_PAGE_SIZE}")
    return pagina, tamanho


def _link_pagina_v3(numero: int) -> str:
    params = request.args.to_dict(flat=False)
    params["page"] = [str(numero)]
    params.pop("pagina", None)
    from urllib.parse import urlencode

    return f"{request.path}?{urlencode(params, doseq=True)}"


def _intervalo_v3() -> tuple[date | None, date | None]:
    try:
        inicio = date.fromisoformat(request.args["inicio"]) if request.args.get("inicio") else None
        fim = date.fromisoformat(request.args["fim"]) if request.args.get("fim") else None
    except ValueError:
        abort(400, "inicio e fim devem usar AAAA-MM-DD")
    if inicio and fim and inicio > fim:
        abort(400, "inicio não pode ser posterior a fim")
    return inicio, fim


def _janela_analitica_v3() -> tuple[date, date]:
    """Resolve a janela de uma série analítica sem aceitar datas futuras."""
    hoje = datetime.now(MARKET_TIMEZONE).date()
    try:
        limite = int(current_app.config["PATRIMONIO_MAX_HISTORICO_DIAS"])
    except (KeyError, TypeError, ValueError):
        abort(503, "Janela histórica do patrimônio não configurada.")
    if limite <= 0:
        abort(503, "Janela histórica do patrimônio inválida.")
    inicio, fim = _intervalo_v3()
    fim = fim or hoje
    inicio = inicio or (fim - timedelta(days=limite))
    if fim > hoje:
        abort(400, "fim não pode ser uma data futura")
    if inicio > fim:
        abort(400, "inicio não pode ser posterior a fim")
    if inicio < fim - timedelta(days=limite):
        abort(400, "inicio fora da janela histórica pública configurada")
    return inicio, fim


def _atividade_v3_transacao(item: Transaction, titular: str) -> dict:
    instrumento = item.ticker
    return {
        "id": _id_v3("atividade-transacao", item.id),
        "origem": "transacao",
        "data": item.closed_on.isoformat(),
        "data_realizacao": item.closed_on.isoformat(),
        "descricao": f"Encerramento de {instrumento}",
        "tipo": "venda" if item.side == Side.BUY else "recompra",
        "status": "realizado",
        "moeda": item.currency,
        "valor": _dinheiro(item.result),
        "valor_realizado": _dinheiro(item.result),
        "instrumento": instrumento,
        "instituicao": identidade(item.broker),
        "titular": titular,
        "deep_link": url_for("portfolio.edit_transaction", transaction_id=item.id),
    }


def _atividade_v3_provento(item: Dividend, titular: str) -> dict:
    return {
        "id": _id_v3("atividade-provento", item.id),
        "origem": "provento",
        "data": item.payment_date.isoformat(),
        "data_realizacao": item.payment_date.isoformat(),
        "descricao": f"{item.kind.value.capitalize()} de {item.ticker}",
        "tipo": item.kind.value,
        "status": "realizado",
        "moeda": item.currency,
        "valor": _dinheiro(item.amount),
        "valor_realizado": _dinheiro(item.amount),
        "instrumento": item.ticker,
        "instituicao": identidade(item.broker),
        "titular": titular,
        "deep_link": url_for("portfolio.edit_dividend", dividend_id=item.id),
    }


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
    """O pedido traz o token certo? Falso quando nenhum token está configurado."""
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


def _owner_id() -> int:
    """Resolve o dono financeiro explicitamente configurado para a publicação."""
    raw = str(current_app.config.get("PATRIMONIO_OWNER_ID") or "").strip()
    try:
        owner_id = int(raw)
    except (TypeError, ValueError):
        owner_id = 0
    if not 0 < owner_id <= 2_147_483_647:
        abort(
            503,
            "Publicação de patrimônio sem owner_id: defina PATRIMONIO_OWNER_ID "
            "para o usuário financeiro autorizado.",
        )
    return owner_id


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


@bp.get("/patrimonio/v1/resumo")
def patrimonio_resumo():
    _exigir_token()
    titular_nome = _titular()
    owner_id = _owner_id()
    foto = _Foto(titular=identidade(titular_nome))

    hoje = datetime.now(MARKET_TIMEZONE).date()
    try:
        max_history_days = int(current_app.config["PATRIMONIO_MAX_HISTORICO_DIAS"])
    except (KeyError, TypeError, ValueError):
        abort(503, "Janela histórica do patrimônio não configurada.")
    if max_history_days <= 0:
        abort(503, "Janela histórica do patrimônio inválida.")
    referencia = _data_pedida(hoje)
    if referencia < hoje - timedelta(days=max_history_days):
        abort(400, "Data fora da janela histórica pública configurada.")
    if referencia == hoje:
        omitidas = _fotografar_hoje(foto, owner_id)
    else:
        omitidas = _fotografar_passado(foto, referencia, owner_id)

    desde = referencia - timedelta(days=JANELA_DE_PROVENTOS_EM_DIAS)
    proventos = []
    for provento in _proventos(desde, referencia, owner_id):
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


@bp.get("/patrimonio/v2/resumo")
def patrimonio_resumo_v2():
    """Dashboard agregado de patrimônio, paralelo e compatível com a v1."""
    _exigir_token()
    titular_nome = _titular()
    owner_id = _owner_id()
    from app.patrimonio.dashboard import build_dashboard, parse_period

    # Esta rota combina diversas consultas. Fixe o isolamento antes da primeira
    # delas para que uma operacao concorrente nao misture posicoes, fluxos e
    # desempenho de instantes diferentes. Se um chamador deliberadamente ja
    # abriu uma transacao, ela precisa oferecer a mesma garantia.
    sessao = db.session()
    if sessao.in_transaction():
        isolamento = sessao.execute(text("SHOW transaction_isolation")).scalar_one()
        if isolamento.replace("_", " ").lower() != "repeatable read":
            raise RuntimeError(
                "patrimonio/v2 exige transacao REPEATABLE READ antes da primeira consulta"
            )
    else:
        sessao.connection(execution_options={"isolation_level": "REPEATABLE READ"})

    hoje = datetime.now(MARKET_TIMEZONE).date()
    try:
        max_history_days = int(current_app.config["PATRIMONIO_MAX_HISTORICO_DIAS"])
    except (KeyError, TypeError, ValueError):
        abort(503, "Janela histórica do patrimônio não configurada.")
    if max_history_days <= 0:
        abort(503, "Janela histórica do patrimônio inválida.")
    pedida = (request.args.get("data") or "").strip()
    if not pedida:
        referencia = hoje
    else:
        try:
            referencia = date.fromisoformat(pedida)
        except ValueError:
            abort(400, "Data inválida: use AAAA-MM-DD.")
        if referencia > hoje:
            abort(400, "Data futura: não há posição nem fechamento para ela.")
        if referencia < hoje - timedelta(days=max_history_days):
            abort(400, "Data fora da janela histórica pública configurada.")
    try:
        periodo = parse_period(request.args.get("periodo"))
    except ValueError as exc:
        abort(400, str(exc))
    inicio_raw = (request.args.get("inicio") or "").strip()
    inicio = None
    if inicio_raw:
        try:
            inicio = date.fromisoformat(inicio_raw)
        except ValueError:
            abort(400, "Início inválido: use AAAA-MM-DD.")
        if inicio > referencia:
            abort(400, "Início posterior à data de referência.")
        if inicio < referencia - timedelta(days=max_history_days):
            abort(400, "Início fora da janela histórica pública configurada.")
    payload = build_dashboard(
        titular=identidade(titular_nome),
        titular_nome=titular_nome,
        owner_id=owner_id,
        reference=referencia,
        period=periodo,
        start_override=inicio,
        max_history_days=max_history_days,
    )
    resposta = jsonify(payload)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.get("/patrimonio/v3/activities")
def patrimonio_activities_v3():
    """Atividades financeiras encerradas, somente leitura.

    A lista é deliberadamente materializada após consultas separadas: os dois
    modelos têm datas diferentes e o contrato precisa ordenar o conjunto
    combinado de forma determinística. Nenhuma linha aberta ou simulada entra.
    """
    _exigir_token()
    titular_nome = _titular()
    owner_id = _owner_id()
    titular = identidade(titular_nome)
    inicio, fim = _intervalo_v3()
    pagina, tamanho = _paginacao_v3()

    transacoes = (
        select(Transaction)
        .join(Transaction.portfolio_ref)
        .where(
            Transaction.owner_id == owner_id,
            Transaction.status == TransactionStatus.CLOSED,
            Portfolio.simulated.is_(False),
        )
        .options(
            joinedload(Transaction.broker_ref),
            joinedload(Transaction.ticker_ref),
            joinedload(Transaction.option_contract_ref).joinedload(OptionContract.ticker_ref),
        )
    )
    proventos = (
        select(Dividend)
        .where(Dividend.owner_id == owner_id)
        .options(joinedload(Dividend.broker_ref), joinedload(Dividend.ticker_ref))
    )
    if inicio:
        transacoes = transacoes.where(Transaction.closed_on >= inicio)
        proventos = proventos.where(Dividend.payment_date >= inicio)
    if fim:
        transacoes = transacoes.where(Transaction.closed_on <= fim)
        proventos = proventos.where(Dividend.payment_date <= fim)

    itens = [
        (item.closed_on, 0, item.id, _atividade_v3_transacao(item, titular))
        for item in db.session.scalars(transacoes).unique().all()
    ] + [
        (item.payment_date, 1, item.id, _atividade_v3_provento(item, titular))
        for item in db.session.scalars(proventos).unique().all()
    ]
    itens.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    total = len(itens)
    inicio_fatia = (pagina - 1) * tamanho
    fatia = itens[inicio_fatia : inicio_fatia + tamanho]
    paginas = (total + tamanho - 1) // tamanho if total else 0
    resposta = jsonify(
        {
            "contrato": CONTRATO_V3,
            "recurso": "atividades",
            "sistema": SISTEMA,
            "gerado_em": datetime.now(UTC).isoformat(),
            "filtros": {
                "inicio": inicio.isoformat() if inicio else None,
                "fim": fim.isoformat() if fim else None,
            },
            "paginacao": {
                "pagina": pagina,
                "tamanho": tamanho,
                "total": total,
                "paginas": paginas,
                "tem_anterior": pagina > 1 and bool(total),
                "tem_proxima": pagina < paginas,
                "anterior": _link_pagina_v3(pagina - 1) if pagina > 1 and bool(total) else None,
                "proxima": _link_pagina_v3(pagina + 1) if pagina < paginas else None,
            },
            "itens": [row[3] for row in fatia],
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.get("/patrimonio/v3/categories")
def patrimonio_categories_v3():
    """Categorias derivadas dos tipos de provento já publicados."""
    _exigir_token()
    owner_id = _owner_id()
    tipos = db.session.scalars(
        select(Dividend.kind)
        .where(Dividend.owner_id == owner_id)
        .distinct()
        .order_by(Dividend.kind)
    ).all()
    itens = [
        {
            "id": _id_v3("categoria", index),
            "nome": tipo.value,
            "natureza": "renda",
            "deep_link": url_for("portfolio.dividends"),
        }
        for index, tipo in enumerate(tipos, start=1)
    ]
    resposta = jsonify(
        {"contrato": CONTRATO_V3, "recurso": "categorias", "sistema": SISTEMA, "gerado_em": datetime.now(UTC).isoformat(), "itens": itens}
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.get("/patrimonio/v3/metadata")
def patrimonio_metadata_v3():
    """Capacidades e limites do exportador, sem inventar catálogo."""
    _exigir_token()
    resposta = jsonify(
        {
            "contrato": CONTRATO_V3,
            "recurso": "metadata",
            "sistema": SISTEMA,
            "gerado_em": datetime.now(UTC).isoformat(),
            "capacidades": {
                "atividades": True,
                "categorias": True,
                "income": True,
                "performance": True,
                "events": True,
                "renda": True,
                "desempenho": True,
                "eventos": True,
                "escrita": False,
                "paginacao_atividades": True,
                "paginacao_income": True,
                "paginacao_events": True,
            },
            "paginacao": {"padrao": V3_PAGE_SIZE, "maximo": V3_MAX_PAGE_SIZE},
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def _pagina_v3(pagina: int, tamanho: int, total: int) -> dict[str, object]:
    """Envelope de paginação compartilhado pelos recursos analíticos."""
    paginas = (total + tamanho - 1) // tamanho if total else 0
    return {
        "pagina": pagina,
        "tamanho": tamanho,
        "total": total,
        "paginas": paginas,
        "tem_anterior": pagina > 1 and bool(total),
        "tem_proxima": pagina < paginas,
        "anterior": _link_pagina_v3(pagina - 1) if pagina > 1 and bool(total) else None,
        "proxima": _link_pagina_v3(pagina + 1) if pagina < paginas else None,
    }


@bp.get("/patrimonio/v3/income")
def patrimonio_income_v3():
    """Renda recebida detalhada, somente leitura e paginada.

    O recurso expõe somente ``Dividend`` já persistido. Não transforma renda
    em caixa nem tenta calcular uma renda implícita a partir da cotação.
    """
    _exigir_token()
    titular = identidade(_titular())
    owner_id = _owner_id()
    inicio, fim = _intervalo_v3()
    pagina, tamanho = _paginacao_v3()
    consulta = (
        select(Dividend)
        .where(Dividend.owner_id == owner_id)
        .options(joinedload(Dividend.broker_ref), joinedload(Dividend.ticker_ref))
    )
    if inicio:
        consulta = consulta.where(Dividend.payment_date >= inicio)
    if fim:
        consulta = consulta.where(Dividend.payment_date <= fim)
    moeda = (request.args.get("moeda") or "").strip().upper()
    if moeda:
        consulta = consulta.join(Dividend.ticker_ref).where(Ticker.currency == moeda)
    tipo = (request.args.get("tipo") or "").strip().lower()
    if tipo:
        consulta = consulta.where(Dividend.kind == tipo)
    itens = list(
        db.session.scalars(consulta.order_by(Dividend.payment_date, Dividend.id)).unique().all()
    )
    total = len(itens)
    inicio_fatia = (pagina - 1) * tamanho
    fatia = itens[inicio_fatia : inicio_fatia + tamanho]
    payload = []
    for item in fatia:
        payload.append(
            {
                "id": _id_v3("renda", item.id),
                "origem": "provento",
                "data": item.payment_date.isoformat(),
                "descricao": f"{item.kind.value.capitalize()} de {item.ticker}",
                "tipo": item.kind.value,
                "status": "realizado",
                "moeda": item.currency,
                "valor": _dinheiro(item.amount),
                "instrumento": item.ticker,
                "instituicao": identidade(item.broker),
                "titular": titular,
                "categoria": {
                    "id": _id_v3_material("categoria", item.kind.value),
                    "nome": item.kind.value,
                    "natureza": "renda",
                },
                "deep_link": url_for("portfolio.edit_dividend", dividend_id=item.id),
            }
        )
    resposta = jsonify(
        {
            "contrato": CONTRATO_V3,
            "recurso": "income",
            "sistema": SISTEMA,
            "gerado_em": datetime.now(UTC).isoformat(),
            "filtros": {
                "inicio": inicio.isoformat() if inicio else None,
                "fim": fim.isoformat() if fim else None,
                "moeda": moeda or None,
                "tipo": tipo or None,
            },
            "paginacao": _pagina_v3(pagina, tamanho, total),
            "itens": payload,
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.get("/patrimonio/v3/performance")
def patrimonio_performance_v3():
    """Séries mensais TWR já calculadas pelo domínio do CRV."""
    _exigir_token()
    titular = identidade(_titular())
    owner_id = _owner_id()
    inicio, fim = _janela_analitica_v3()
    from app.patrimonio.dashboard import _performance

    dividends = queries.dividends(inicio, fim, owner_id)
    series = _performance(fim, inicio, dividends, owner_id)
    itens = [
        {
            "id": _id_v3_material("performance", f"{item['moeda']}:{inicio}:{fim}"),
            "titular": titular,
            "moeda": item["moeda"],
            "metodo": item["metodo"],
            "inicio": item["inicio"],
            "fim": item["fim"],
            "pontos": item["pontos"],
            "deep_link": item["endereco"],
        }
        for item in series
    ]
    resposta = jsonify(
        {
            "contrato": CONTRATO_V3,
            "recurso": "performance",
            "sistema": SISTEMA,
            "gerado_em": datetime.now(UTC).isoformat(),
            "filtros": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
            "itens": itens,
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.get("/patrimonio/v3/events")
def patrimonio_events_v3():
    """Eventos de quantidade usados para a série de performance.

    O contrato publica a quantidade resultante, não preço ou patrimônio. A
    ausência de preço é deliberada: o preço pertence à série de cotações e
    não deve ser inferido pelo consumidor.
    """
    _exigir_token()
    titular = identidade(_titular())
    owner_id = _owner_id()
    inicio, fim = _janela_analitica_v3()
    pagina, tamanho = _paginacao_v3()
    eventos = [
        event
        for event in queries.performance_events(fim, owner_id)
        if inicio <= event.occurred_on <= fim
    ]
    tickers = queries.tickers(event.ticker_id for event in eventos)
    # A ordem inclui todos os campos do evento para permanecer determinística
    # mesmo quando duas linhas têm a mesma data e quantidade.
    eventos.sort(
        key=lambda event: (
            event.occurred_on,
            event.position_key[0],
            event.position_key[1],
            event.ticker_id,
            event.resulting_signed_quantity,
        ),
        reverse=True,
    )
    ocorrencias: dict[str, int] = {}
    itens = []
    for event in eventos:
        ticker = tickers.get(event.ticker_id)
        if ticker is None:
            continue
        classe, posicao_id = event.position_key
        material = (
            f"{event.occurred_on.isoformat()}:{classe}:{posicao_id}:"
            f"{event.ticker_id}:{event.resulting_signed_quantity}"
        )
        ocorrencias[material] = ocorrencias.get(material, 0) + 1
        material = f"{material}:{ocorrencias[material]}"
        deep_link = (
            url_for("portfolio.position_detail", position_id=posicao_id)
            if classe == "stock"
            else url_for("options.edit_position", position_id=posicao_id)
        )
        itens.append(
            {
                "id": _id_v3_material("evento", material),
                "titular": titular,
                "data": event.occurred_on.isoformat(),
                "tipo": "movimentacao",
                "status": "realizado",
                "classe": classe,
                "instrumento": ticker.symbol,
                "moeda": ticker.currency,
                "mercado": ticker.market.value,
                "quantidade_resultante": _numero(event.resulting_signed_quantity),
                "deep_link": deep_link,
            }
        )
    total = len(itens)
    inicio_fatia = (pagina - 1) * tamanho
    resposta = jsonify(
        {
            "contrato": CONTRATO_V3,
            "recurso": "events",
            "sistema": SISTEMA,
            "gerado_em": datetime.now(UTC).isoformat(),
            "filtros": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
            "paginacao": _pagina_v3(pagina, tamanho, total),
            "itens": itens[inicio_fatia : inicio_fatia + tamanho],
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta
