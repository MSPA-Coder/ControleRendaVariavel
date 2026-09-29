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
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from flask import abort, current_app, jsonify, request, url_for
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import joinedload

from app import db
from app.core.domain import MARKET_TIMEZONE
from app.models import (
    Broker,
    Dividend,
    Market,
    OptionContract,
    OptionPosition,
    PatrimonioV4ChangeCounter,
    PatrimonioV4Outbox,
    Portfolio,
    Position,
    PositionMovementArchive,
    Quote,
    QuoteHistory,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
)
from app.patrimonio import queries
from app.patrimonio.fotografia import (
    SISTEMA,
    _dinheiro,
    _Foto,
    _fotografar_hoje,
    _fotografar_passado,
    _numero,
    _proventos,
    identidade,
)
from app.positions.holdings_history import QuantityTimeline, closing_price_on
from app.positions.portfolio import effective_position_quote
from app.routes import bp

CONTRATO = "patrimonio/v1"
CONTRATO_V3 = "patrimonio/v3"
CONTRATO_V4 = "patrimonio/v4"
V3_PAGE_SIZE = 50
V3_MAX_PAGE_SIZE = 100
V4_MAX_CHANGE_LIMIT = 500

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


def token_valido_apresentado(nome_config: str = "PATRIMONIO_TOKEN") -> bool:
    """O pedido traz o token certo? Falso quando nenhum token está configurado."""
    configurado = str(current_app.config.get(nome_config) or "")
    if not configurado:
        return False
    apresentado = request.headers.get("Authorization", "")
    return hmac.compare_digest(apresentado, f"Bearer {configurado}")


def _exigir_token(*, v4: bool = False) -> None:
    """Mesmo contrato do agente do coletor, e pelas mesmas razões.

    503 quando ninguém configurou a integração aqui; 401 quando o token está
    errado. A diferença importa: dizer 401 a quem nunca recebeu token mandaria o
    operador procurar por horas um segredo que nunca foi concedido.
    """
    nome_config = "PATRIMONIO_INTEGRATION_TOKEN" if v4 else "PATRIMONIO_TOKEN"
    if not current_app.config.get(nome_config):
        abort(503, "Publicação de patrimônio não configurada.")
    if not token_valido_apresentado(nome_config):
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


def _watermark_v4() -> int:
    return int(db.session.scalar(select(PatrimonioV4ChangeCounter.value).where(
        PatrimonioV4ChangeCounter.id == 1
    )) or 0)


def _cursor_v4(cursor: int, owner_id: int) -> str:
    material = f"v1:{SISTEMA}:{owner_id}:{cursor}".encode()
    secret = str(current_app.config["PATRIMONIO_INTEGRATION_TOKEN"]).encode()
    signature = hmac.new(secret, b"patrimonio-v4-cursor:" + material, hashlib.sha256).digest()
    return urlsafe_b64encode(material + b"." + signature).decode().rstrip("=")


def _cursor_v4_ler(raw: str | None, owner_id: int) -> int:
    if not raw or len(raw) > 256:
        return 0 if not raw else abort(400, "Cursor inválido.")
    try:
        decoded = urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        material, signature = decoded.rsplit(b".", 1)
        expected = hmac.new(
            str(current_app.config["PATRIMONIO_INTEGRATION_TOKEN"]).encode(),
            b"patrimonio-v4-cursor:" + material,
            hashlib.sha256,
        ).digest()
        version, source, bound_owner, position = material.decode().split(":")
        cursor = int(position)
    except (UnicodeDecodeError, ValueError, TypeError):
        abort(400, "Cursor inválido.")
    if not hmac.compare_digest(signature, expected) or (
        version, source, bound_owner
    ) != ("v1", SISTEMA, str(owner_id)) or cursor < 0:
        abort(400, "Cursor inválido.")
    return cursor


def _change_limit_v4() -> int:
    try:
        limit = int(request.args.get("limit", "100"))
    except (TypeError, ValueError):
        abort(400, "limit deve ser inteiro entre 1 e 500.")
    if not 1 <= limit <= V4_MAX_CHANGE_LIMIT:
        abort(400, "limit deve ser inteiro entre 1 e 500.")
    return limit


def _change_source_id_v4(item: PatrimonioV4Outbox) -> str:
    return _id_v3_material("change-target", f"{item.resource}:{item.source_record_id}")


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


@bp.get("/patrimonio/v4/ledger")
def patrimonio_ledger_v4():
    """Ledger de posições de ações encerradas, com cobertura prospectiva.

    Cada linha é um movimento arquivado; ``close`` é derivado da transação
    final e zera a quantidade. O recurso não publica lançamentos de caixa: o
    Controle Bancário continua sendo a fonte para esse lado da operação.
    """
    _exigir_token(v4=True)
    owner_id = _owner_id()
    pagina, tamanho = _paginacao_v3()
    filtros = PositionMovementArchive.owner_id == owner_id
    total = db.session.scalar(
        select(func.count(PositionMovementArchive.id)).where(filtros)
    ) or 0
    linhas = db.session.execute(
        select(PositionMovementArchive, Ticker, Broker, Portfolio)
        .join(Ticker, Ticker.id == PositionMovementArchive.ticker_id)
        .join(Broker, Broker.id == PositionMovementArchive.broker_id)
        .join(Portfolio, Portfolio.id == PositionMovementArchive.portfolio_id)
        .where(filtros)
        .order_by(
            PositionMovementArchive.occurred_on,
            PositionMovementArchive.source_position_id,
            PositionMovementArchive.id,
        )
        .offset((pagina - 1) * tamanho)
        .limit(tamanho)
    ).all()
    itens = []
    for linha, ticker, corretora, carteira in linhas:
        chave_movimento = (
            str(linha.source_movement_id)
            if linha.source_movement_id is not None
            else f"close:{linha.source_transaction_id}"
        )
        source_id = _id_v3_material(
            "ledger-event", f"{linha.source_position_id}:{chave_movimento}"
        )
        lado_posicao = "long" if linha.side == Side.BUY.value else "short"
        reduz = linha.kind in {"decrease", "close"}
        lado_execucao = None
        if linha.kind != "adjustment":
            lado_execucao = (
                ("buy" if lado_posicao == "long" else "sell")
                if not reduz
                else ("sell" if lado_posicao == "long" else "buy")
            )
        direcao = Decimal("1") if lado_posicao == "long" else Decimal("-1")
        item = {
            "id": source_id,
            "source_id": source_id,
            "titular": identidade(_titular()),
            "date": linha.occurred_on.isoformat(),
            "kind": linha.kind,
            "instrument": ticker.symbol,
            "instrument_type": "equity",
            "market": ticker.market.value,
            "currency": ticker.currency,
            "account": identidade(corretora.name),
            "account_name": corretora.name,
            "portfolio": carteira.name,
            "position_side": lado_posicao,
            "quantity_delta": _numero(direcao * linha.quantity_delta),
            "resulting_quantity": _numero(direcao * linha.resulting_quantity),
            "price": _numero(linha.price),
            "average_cost_after": _numero(linha.resulting_average_cost),
            "realized_result": _dinheiro(linha.result) if linha.result is not None else None,
            "execution_side": lado_execucao,
            "source_transaction_id": (
                _id_v3("ledger-transaction", linha.source_transaction_id)
                if linha.source_transaction_id is not None
                else None
            ),
            "cash_accounting_role": "not_a_cash_entry",
        }
        itens.append(item)
    resposta = jsonify(
        {
            "contrato": CONTRATO_V4,
            "recurso": "ledger",
            "sistema": SISTEMA,
            "gerado_em": datetime.now(UTC).isoformat(),
            "estado": "ok" if total else "empty",
            "paginacao": _pagina_v3(pagina, tamanho, total),
            "coverage": {
                "completeness": "prospective",
                "since_revision": "20260928_0024",
                "backfill": False,
                "scope": "posições de ações encerradas totalmente após a revisão 20260928_0024",
                "close_event": "derivado da transação final e saldo resultante zero",
            },
            "itens": itens,
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


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
                "holding_history": True,
                "renda": True,
                "desempenho": True,
                "eventos": True,
                "escrita": False,
                "paginacao_atividades": True,
                "paginacao_income": True,
                "paginacao_events": True,
                "paginacao_holding_history": True,
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


@bp.get("/patrimonio/v3/holding-history")
def patrimonio_holding_history_v3():
    """Série histórica de preço e valor de mercado de um ticker detido.

    A quantidade é reconstruída a partir do extrato real do owner. A resposta
    publica somente datas com fechamento válido e quantidade diferente de
    zero; nunca estima preço nem inclui posições simuladas ou opções.
    """
    _exigir_token()
    titular = identidade(_titular())
    owner_id = _owner_id()
    inicio, fim = _janela_analitica_v3()
    pagina, tamanho = _paginacao_v3()

    symbol = (request.args.get("ticker") or "").strip().upper()
    if not symbol:
        abort(400, "ticker é obrigatório")
    raw_market = (request.args.get("mercado") or "").strip().upper()
    if raw_market and raw_market not in {market.value for market in Market}:
        abort(400, "mercado deve ser B3, NYSE ou NASDAQ")
    ticker = queries.ticker_by_symbol(symbol, raw_market or None)
    if ticker is None:
        abort(404, "Ticker não encontrado")

    # `performance_events` já restringe posições reais ao owner informado;
    # descartar option evita misturar contratos com ações do mesmo ticker.
    eventos = [
        event
        for event in queries.performance_events(fim, owner_id)
        if event.ticker_id == ticker.id and event.position_key[0] == "stock"
    ]
    if not eventos:
        # Também impede que o endpoint vire uma forma de consultar todo o
        # catálogo global de preços por meio de um token de integração.
        abort(404, "Ticker não pertence à carteira publicada")

    timeline = QuantityTimeline(eventos)
    serie = queries.quote_series(
        [ticker.id], start=inicio - timedelta(days=7), end=fim
    )[ticker.id]
    datas = {inicio, fim}
    datas.update(data for data, _preco in serie if inicio <= data <= fim)
    datas.update(event.occurred_on for event in eventos if inicio <= event.occurred_on <= fim)

    pontos = []
    for dia in sorted(datas):
        fechamento = closing_price_on(serie, dia)
        quantidade = timeline.quantity_at(ticker.id, dia)
        if fechamento is None or quantidade == 0:
            continue
        preco_em, preco = fechamento
        pontos.append(
            {
                "data": dia.isoformat(),
                "preco": _numero(preco),
                "preco_em": preco_em.isoformat(),
                "quantidade": _numero(quantidade),
                "valor": _dinheiro(preco * quantidade),
                "moeda": ticker.currency,
            }
        )

    total = len(pontos)
    inicio_fatia = (pagina - 1) * tamanho
    resposta = jsonify(
        {
            "contrato": CONTRATO_V3,
            "recurso": "holding-history",
            "sistema": SISTEMA,
            "gerado_em": datetime.now(UTC).isoformat(),
            "estado": "ok" if total else "empty",
            "titular": titular,
            "ticker": ticker.symbol,
            "mercado": ticker.market.value,
            "moeda": ticker.currency,
            "filtros": {
                "ticker": ticker.symbol,
                "mercado": ticker.market.value,
                "inicio": inicio.isoformat(),
                "fim": fim.isoformat(),
            },
            "paginacao": _pagina_v3(pagina, tamanho, total),
            "itens": pontos[inicio_fatia : inicio_fatia + tamanho],
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.get("/patrimonio/v4/metadata")
def patrimonio_metadata_v4():
    """Descreve o retrato inicial e declara limites do que este publicador sabe."""
    _exigir_token(v4=True)
    try:
        historico_dias = int(current_app.config["PATRIMONIO_MAX_HISTORICO_DIAS"])
    except (KeyError, TypeError, ValueError):
        abort(503, "Janela histórica do patrimônio não configurada.")
    if historico_dias <= 0:
        abort(503, "Janela histórica do patrimônio inválida.")
    resposta = jsonify(
        {
            "contrato": CONTRATO_V4,
            "recurso": "metadata",
            "sistema": SISTEMA,
            "source_id": _id_v3_material("source", SISTEMA),
            "gerado_em": datetime.now(UTC).isoformat(),
            "high_watermark": _cursor_v4(_watermark_v4(), _owner_id()),
            "capacidades": {
                "snapshot": True,
                "holdings": True,
                "position_ledger": True,
                "income": True,
                "prices": True,
                "options": False,
                "complete_trades": False,
                "changes": True,
            },
            "coverage": {
                "holdings": {
                    "available": True,
                    "scope": "posições abertas de ações em carteiras reais do owner configurado",
                    "includes_unpriced": True,
                },
                "income": {
                    "available": True,
                    "scope": "proventos persistidos do owner configurado",
                    "amount_semantics": "valor efetivamente recebido; impostos não discriminados",
                    "role": "fato analítico; pode também existir no Controle Bancário",
                    "do_not_rebook_as_cash": True,
                },
                "prices": {
                    "available": True,
                    "scope": "cotações apenas de instrumentos já detidos pelo owner",
                    "history_days_limit": historico_dias,
                    "history_window_guaranteed": False,
                },
                "options": {"available": False, "reason": "não publicadas neste contrato"},
                "complete_trades": {
                    "available": False,
                    "reason": "o ledger detalhado é prospectivo; posições encerradas antes da revisão 20260928_0024 não têm backfill",
                },
                "position_ledger": {
                    "available": True,
                    "endpoint": "/patrimonio/v4/ledger",
                    "scope": "movimentos de posições de ações encerradas após a revisão 20260928_0024",
                    "completeness": "prospective",
                    "since_revision": "20260928_0024",
                    "backfill": False,
                    "close_event": "derivado da transação final do encerramento total",
                    "cash_accounting_role": "not_a_cash_entry",
                },
                "changes": {
                    "available": True,
                    "endpoint": "/patrimonio/v4/changes",
                    "mode": "snapshot_invalidation",
                    "high_watermark": _cursor_v4(_watermark_v4(), _owner_id()),
                },
            },
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.get("/patrimonio/v4/changes")
def patrimonio_changes_v4():
    """Invalidações ordenadas; o consumidor reconcilia pelo snapshot v4."""
    _exigir_token(v4=True)
    owner_id = _owner_id()
    after = _cursor_v4_ler(request.args.get("after"), owner_id)
    limit = _change_limit_v4()
    sessao = db.session()
    if sessao.in_transaction():
        isolamento = sessao.execute(text("SHOW transaction_isolation")).scalar_one()
        if isolamento.replace("_", " ").lower() != "repeatable read":
            raise RuntimeError("patrimonio/v4 exige transacao REPEATABLE READ")
    else:
        sessao.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    watermark = _watermark_v4()
    rows = db.session.scalars(
        select(PatrimonioV4Outbox)
        .where(
            PatrimonioV4Outbox.cursor > after,
            PatrimonioV4Outbox.cursor <= watermark,
            or_(
                PatrimonioV4Outbox.owner_id == owner_id,
                PatrimonioV4Outbox.owner_id.is_(None),
            ),
        )
        .order_by(PatrimonioV4Outbox.cursor)
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_position = rows[-1].cursor if rows else watermark
    items = [
        {
            "cursor": _cursor_v4(item.cursor, owner_id),
            "resource": item.resource,
            "source_id": _change_source_id_v4(item),
            "operation": item.operation,
            "changed_at": item.changed_at.isoformat(),
            "payload": {"mode": "snapshot_required"} if item.operation == "upsert" else None,
        }
        for item in rows
    ]
    resposta = jsonify(
        {
            "contrato": CONTRATO_V4,
            "recurso": "changes",
            "sistema": SISTEMA,
            "mode": "snapshot_invalidation",
            "high_watermark": _cursor_v4(watermark, owner_id),
            "next_cursor": _cursor_v4(next_position, owner_id),
            "has_more": has_more,
            "items": items,
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.get("/patrimonio/v4/snapshot")
def patrimonio_snapshot_v4():
    """Publica um retrato completo atual, sem simular eventos de negociação."""
    _exigir_token(v4=True)
    include_prices = request.args.get("include_prices", "true").strip().lower()
    if include_prices not in {"true", "false"}:
        abort(400, "include_prices deve ser true ou false.")
    include_prices = include_prices == "true"
    titular = identidade(_titular())
    owner_id = _owner_id()
    sessao = db.session()
    if sessao.in_transaction():
        isolamento = sessao.execute(text("SHOW transaction_isolation")).scalar_one()
        if isolamento.replace("_", " ").lower() != "repeatable read":
            raise RuntimeError("patrimonio/v4 exige transacao REPEATABLE READ")
    else:
        sessao.connection(execution_options={"isolation_level": "REPEATABLE READ"})

    gerado_em = datetime.now(UTC)
    watermark = _cursor_v4(_watermark_v4(), owner_id)
    snapshot_id = _id_v3_material("snapshot", gerado_em.isoformat() + ":" + uuid4().hex)
    hoje = datetime.now(MARKET_TIMEZONE).date()
    try:
        historico_dias = int(current_app.config["PATRIMONIO_MAX_HISTORICO_DIAS"])
    except (KeyError, TypeError, ValueError):
        abort(503, "Janela histórica do patrimônio não configurada.")
    if historico_dias <= 0:
        abort(503, "Janela histórica do patrimônio inválida.")
    inicio_historico = hoje - timedelta(days=historico_dias)

    # A linha do tempo também inclui posições encerradas arquivadas. Isso
    # restringe a publicação de preços a instrumentos que o owner já deteve,
    # sem expor o catálogo global de cotações.
    ticker_ids = {
        evento.ticker_id
        for evento in queries.performance_events(hoje, owner_id)
        if evento.position_key[0] == "stock"
    }
    posicoes = queries.real_positions(owner_id)
    corretoras = {}
    holdings = []
    unpriced_holdings = 0
    for posicao in posicoes:
        ticker_ids.add(posicao.ticker_id)
        corretora_id = identidade(posicao.broker)
        corretoras.setdefault(corretora_id, posicao.broker)
        sinal = Decimal("1") if posicao.side == Side.BUY else Decimal("-1")
        preco = None
        preco_em = None
        situacao_do_preco = None
        if posicao.quote is not None:
            preco, observado_em = effective_position_quote(posicao)
            preco_em = observado_em.isoformat()
            situacao_do_preco = posicao.quote.source_status
        if preco is None:
            unpriced_holdings += 1
            price_kind = "unavailable"
        elif posicao.side == Side.BUY and posicao.quote.buy_price is not None:
            price_kind = "side_specific_buy_quote"
        elif posicao.side == Side.SELL and posicao.quote.sell_price is not None:
            price_kind = "side_specific_sell_quote"
        else:
            price_kind = "last_price_fallback"
        holding_id = _id_v3("holding", posicao.id)
        holdings.append(
            {
                "id": holding_id,
                "source_id": holding_id,
                "snapshot_id": snapshot_id,
                "titular": titular,
                "account": corretora_id,
                "account_name": posicao.broker,
                "portfolio": posicao.portfolio_ref.name,
                "instrument": posicao.ticker,
                "instrument_type": "equity",
                "market": posicao.ticker_ref.market.value,
                "currency": posicao.currency,
                "side": "long" if sinal > 0 else "short",
                "quantity": _numero(sinal * posicao.quantity),
                "average_cost": _numero(posicao.average_cost),
                "current_price": _numero(preco) if preco is not None else None,
                "market_value": _dinheiro(sinal * posicao.quantity * preco) if preco is not None else None,
                "price_kind": price_kind,
                "valuation_method": (
                    "unavailable_no_price"
                    if preco is None
                    else "signed_quantity_times_side_specific_price_or_fallback"
                ),
                "price_observed_at": preco_em,
                "price_status": situacao_do_preco,
                "deep_link": url_for("portfolio.position_detail", position_id=posicao.id),
            }
        )

    quotes = []
    history = []
    if ticker_ids and include_prices:
        for ticker, quote in db.session.execute(
            select(Ticker, Quote)
            .join(Quote, Quote.ticker_id == Ticker.id)
            .where(Ticker.id.in_(ticker_ids))
            .order_by(Ticker.symbol)
        ):
            price_id = _id_v3_material("price-current", str(ticker.id))
            quotes.append(
                {
                    "id": price_id,
                    "source_id": price_id,
                    "snapshot_id": snapshot_id,
                    "instrument": ticker.symbol,
                    "market": ticker.market.value,
                    "currency": ticker.currency,
                    "price": _numero(quote.last_price),
                    "observed_at": quote.observed_at.isoformat(),
                    "status": quote.source_status,
                    "price_kind": "collector_last_price",
                    "valuation_method": "not_used_for_position_valuation",
                }
            )
        for ticker, point in db.session.execute(
            select(Ticker, QuoteHistory)
            .join(QuoteHistory, QuoteHistory.ticker_id == Ticker.id)
            .where(
                Ticker.id.in_(ticker_ids),
                QuoteHistory.recorded_date >= inicio_historico,
                QuoteHistory.recorded_date <= hoje,
            )
            .order_by(Ticker.symbol, QuoteHistory.recorded_date)
        ):
            price_id = _id_v3_material(
                "price-history", f"{ticker.id}:{point.recorded_date.isoformat()}"
            )
            history.append(
                {
                    "id": price_id,
                    "source_id": price_id,
                    "snapshot_id": snapshot_id,
                    "instrument": ticker.symbol,
                    "market": ticker.market.value,
                    "currency": ticker.currency,
                    "price": _numero(point.price),
                    "date": point.recorded_date.isoformat(),
                    "observed_at": point.recorded_at.isoformat(),
                    "price_kind": "daily_last_observation",
                    "valuation_method": "not_used_for_position_valuation",
                }
            )

    proventos = db.session.scalars(
        select(Dividend)
        .where(Dividend.owner_id == owner_id)
        .options(joinedload(Dividend.broker_ref), joinedload(Dividend.ticker_ref))
        .order_by(Dividend.payment_date, Dividend.id)
    ).unique().all()
    for item in proventos:
        corretoras.setdefault(identidade(item.broker), item.broker)
    income = []
    for item in proventos:
        income_id = _id_v3("income", item.id)
        income.append(
            {
                "id": income_id,
                "source_id": income_id,
                "snapshot_id": snapshot_id,
                "date": item.payment_date.isoformat(),
                "instrument": item.ticker,
                "kind": item.kind.value,
                "currency": item.currency,
                "amount": _dinheiro(item.amount),
                "role": "analytic_only",
                "cash_accounting_role": "not_a_cash_entry",
                "duplicate_risk": "may_also_be_recorded_in_controle_bancario",
                "account": identidade(item.broker),
                "account_name": item.broker,
                "titular": titular,
                "deep_link": url_for("portfolio.edit_dividend", dividend_id=item.id),
            }
        )
    simulated_equities = int(
        db.session.scalar(
            select(func.count(Position.id))
            .join(Position.portfolio_ref)
            .where(Position.owner_id == owner_id, Portfolio.simulated.is_(True))
        )
        or 0
    )
    simulated_options = int(
        db.session.scalar(
            select(func.count(OptionPosition.id))
            .join(OptionPosition.portfolio_ref)
            .where(OptionPosition.owner_id == owner_id, Portfolio.simulated.is_(True))
        )
        or 0
    )
    real_options = int(
        db.session.scalar(
            select(func.count(OptionPosition.id))
            .join(OptionPosition.portfolio_ref)
            .where(OptionPosition.owner_id == owner_id, Portfolio.simulated.is_(False))
        )
        or 0
    )
    resposta = jsonify(
        {
            "contrato": CONTRATO_V4,
            "recurso": "snapshot",
            "sistema": SISTEMA,
            "source_id": _id_v3_material("source", SISTEMA),
            "snapshot_id": snapshot_id,
            "gerado_em": gerado_em.isoformat(),
            "data_de_referencia": hoje.isoformat(),
            "high_watermark": watermark,
            "capacidades": {
                "holdings": True,
                "position_ledger": True,
                "income": True,
                "prices": True,
                "options": False,
                "complete_trades": False,
                "changes": True,
            },
            "coverage": {
                "holdings": {
                    "included": len(holdings),
                    "unpriced": unpriced_holdings,
                    "complete": True,
                    "scope": "posições abertas de ações em carteiras reais; opções e simuladas excluídas",
                },
                "income": {
                    "included": len(income),
                    "scope": "todos os proventos persistidos do owner",
                    "role": "fato analítico; pode também existir no Controle Bancário",
                    "do_not_rebook_as_cash": True,
                },
                "prices": {
                    "current_included": len(quotes),
                    "history_included": len(history),
                    "complete": include_prices,
                    "omitted": None if include_prices else "consumer_requested",
                    "history_start": inicio_historico.isoformat(),
                    "history_end": hoje.isoformat(),
                    "history_days_limit": historico_dias,
                    "history_window_guaranteed": False,
                    "scope": "somente tickers já detidos em posição real",
                },
                "options": {
                    "available": False,
                    "excluded_options": real_options,
                    "excluded_simulated": simulated_options,
                },
                "exclusions": {
                    "unpriced_holdings": unpriced_holdings,
                    "excluded_simulated": simulated_equities + simulated_options,
                    "excluded_simulated_equities": simulated_equities,
                    "excluded_simulated_options": simulated_options,
                    "excluded_options": real_options,
                },
                "complete_trades": {"available": False},
                "position_ledger": {
                    "available": True,
                    "endpoint": "/patrimonio/v4/ledger",
                    "completeness": "prospective",
                    "since_revision": "20260928_0024",
                    "backfill": False,
                },
                "changes": {
                    "available": True,
                    "endpoint": "/patrimonio/v4/changes",
                    "mode": "snapshot_invalidation",
                    "high_watermark": watermark,
                },
            },
            "accounts": [
                {
                    "id": chave,
                    "source_id": _id_v3_material("account", chave),
                    "snapshot_id": snapshot_id,
                    "name": nome,
                    "type": "brokerage",
                }
                for chave, nome in sorted(corretoras.items())
            ],
            "holdings": holdings,
            "income": income,
            "prices": {"current": quotes, "history": history},
        }
    )
    resposta.headers["Cache-Control"] = "no-store"
    return resposta
