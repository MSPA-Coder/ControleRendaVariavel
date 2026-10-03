"""O que este sistema publica para o consolidador de patrimônio: o contrato `patrimonio/v4`.

É a metade gêmea do que o Controle Bancário publica: ele publica o **caixa**;
este publica o **investimento**. Quem soma (hoje, o Wealthfolio) é um terceiro
aplicativo, só de leitura, que não toca no banco de nenhum dos dois -- ler o
banco alheio acoplaria os schemas e quebraria a cada migration. As rotas são
`metadata`, `snapshot`, `changes`, `ledger` e `activities`, todas com o token da
integração (`PATRIMONIO_INTEGRATION_TOKEN`).

Os contratos `patrimonio/v1` a `v3` (resumo, dashboard, atividades, categorias,
renda, desempenho, eventos e histórico por posição) serviam ao NetWorth,
aposentado em 29/09/2026, e foram retirados em 03/10/2026, junto com o
`PATRIMONIO_TOKEN` que os autorizava.

O VOCABULÁRIO COMUM

Titular e instituição são identificados pelo **nome normalizado**, não pelo id
do banco: "Genial" é a corretora 1 aqui e outra coisa lá, mas é a mesma
corretora para quem lê. `identidade()` tem de produzir exatamente o mesmo
resultado dos dois lados -- se um normalizar "Itaú" e o outro "itau", viram dois
titulares e o patrimônio aparece **dobrado**. Os dois repositórios têm o mesmo
teste, com os mesmos casos, justamente para isso não derivar em silêncio.

Todo valor viaja como **texto**: `float` não representa 0,10 e quem consolida
somaria centavos que nunca existiram.

O QUE ELE OMITE, E DIZ QUE OMITIU

1. **Carteira simulada.** Metade das posições desta base está numa, e somá-la ao
   patrimônio o infla com dinheiro que não existe -- sem que o número deixe de
   parecer plausível;
2. **Opções.** Elas têm valor e ficam de fora até serem publicadas com o mesmo
   cuidado das ações. Enquanto isso, a contagem aparece na cobertura;
3. **Posição sem cotação.** Sem preço não há valor a mercado, e inventar um
   seria pior do que faltar.

Omissão contada é omissão visível; omissão silenciosa é um patrimônio errado com
cara de completo.
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
    dinheiro,
    identidade,
    numero,
)
from app.positions.portfolio import effective_position_quote
from app.routes import bp

CONTRATO_V4 = "patrimonio/v4"
V3_PAGE_SIZE = 50
V3_MAX_PAGE_SIZE = 100
V4_MAX_CHANGE_LIMIT = 500


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
        "valor": dinheiro(item.result),
        "valor_realizado": dinheiro(item.result),
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
        "valor": dinheiro(item.amount),
        "valor_realizado": dinheiro(item.amount),
        "instrumento": item.ticker,
        "instituicao": identidade(item.broker),
        "titular": titular,
        "deep_link": url_for("portfolio.edit_dividend", dividend_id=item.id),
    }


TOKEN_DA_INTEGRACAO = "PATRIMONIO_INTEGRATION_TOKEN"


def token_valido_apresentado(nome_config: str = TOKEN_DA_INTEGRACAO) -> bool:
    """O pedido traz o token certo? Falso quando nenhum token está configurado."""
    configurado = str(current_app.config.get(nome_config) or "")
    if not configurado:
        return False
    apresentado = request.headers.get("Authorization", "")
    return hmac.compare_digest(apresentado, f"Bearer {configurado}")


def _exigir_token() -> None:
    """Mesmo contrato do agente do coletor, e pelas mesmas razões.

    503 quando ninguém configurou a integração aqui; 401 quando o token está
    errado. A diferença importa: dizer 401 a quem nunca recebeu token mandaria o
    operador procurar por horas um segredo que nunca foi concedido.

    O `PATRIMONIO_TOKEN` antigo foi retirado com os contratos v1 a v3 que só ele
    autorizava (03/10/2026).
    """
    if not current_app.config.get(TOKEN_DA_INTEGRACAO):
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
        if len(decoded) < 34 or decoded[-33:-32] != b".":
            abort(400, "Cursor inválido.")
        material, signature = decoded[:-33], decoded[-32:]
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



@bp.get("/patrimonio/v4/ledger")
def patrimonio_ledger_v4():
    """Ledger de posições de ações encerradas, com cobertura prospectiva.

    Cada linha é um movimento arquivado; ``close`` é derivado da transação
    final e zera a quantidade. O recurso não publica lançamentos de caixa: o
    Controle Bancário continua sendo a fonte para esse lado da operação.
    """
    _exigir_token()
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
            "quantity_delta": numero(direcao * linha.quantity_delta),
            "resulting_quantity": numero(direcao * linha.resulting_quantity),
            "price": numero(linha.price),
            "average_cost_after": numero(linha.resulting_average_cost),
            "realized_result": dinheiro(linha.result) if linha.result is not None else None,
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



@bp.get("/patrimonio/v4/activities")
def patrimonio_activities_v4():
    """Atividades encerradas autenticadas pelo token exclusivo v4."""
    _exigir_token()
    return _atividades_publicadas(CONTRATO_V4)


def _atividades_publicadas(contrato: str):
    """Atividades financeiras encerradas, somente leitura e paginadas.

    A lista é deliberadamente materializada após consultas separadas: os dois
    modelos têm datas diferentes e o contrato precisa ordenar o conjunto
    combinado de forma determinística. Nenhuma linha aberta ou simulada entra.
    Transações são resumos de resultado e não têm os lotes de execução.
    """
    titular_nome = _titular()
    owner_id = _owner_id()
    titular = identidade(titular_nome)
    high_watermark = None
    if contrato == CONTRATO_V4:
        sessao = db.session()
        if sessao.in_transaction():
            isolamento = sessao.execute(text("SHOW transaction_isolation")).scalar_one()
            if isolamento.replace("_", " ").lower() != "repeatable read":
                raise RuntimeError("patrimonio/v4 exige transacao REPEATABLE READ")
        else:
            sessao.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        high_watermark = _cursor_v4(_watermark_v4(), owner_id)
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

    transacoes = db.session.scalars(transacoes).unique().all()
    proventos = db.session.scalars(proventos).unique().all()
    itens = [
        (item.closed_on, 0, item.id, _atividade_v3_transacao(item, titular))
        for item in transacoes
    ] + [
        (item.payment_date, 1, item.id, _atividade_v3_provento(item, titular))
        for item in proventos
    ]
    itens.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    total = len(itens)
    inicio_fatia = (pagina - 1) * tamanho
    fatia = itens[inicio_fatia : inicio_fatia + tamanho]
    paginas = (total + tamanho - 1) // tamanho if total else 0
    payload = {
        "contrato": contrato,
        "recurso": "activities" if contrato == CONTRATO_V4 else "atividades",
        "sistema": SISTEMA,
        "gerado_em": datetime.now(UTC).isoformat(),
        **({"high_watermark": high_watermark} if high_watermark is not None else {}),
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
    if contrato == CONTRATO_V4:
        payload["coverage"] = {
            "complete": inicio is None and fim is None,
            "scope": "resultados de transações encerradas e proventos persistidos do owner configurado",
            "closed_transactions_included": len(transacoes),
            "income_included": len(proventos),
            "limitations": "transações são resumos de resultado; não incluem lotes, preços de execução ou eventos de abertura",
        }
    resposta = jsonify(payload)
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



@bp.get("/patrimonio/v4/metadata")
def patrimonio_metadata_v4():
    """Descreve o retrato inicial e declara limites do que este publicador sabe."""
    _exigir_token()
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
                "activities": True,
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
                "activities": {
                    "available": True,
                    "endpoint": "/patrimonio/v4/activities",
                    "scope": "resumos de transações encerradas e proventos persistidos",
                    "trade_details": False,
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
                    "reason": "atividades publicam somente resultados agregados de encerramentos; não incluem lotes ou preços de execução; o ledger detalhado é prospectivo e não tem backfill anterior",
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
    _exigir_token()
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
    _exigir_token()
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
                "quantity": numero(sinal * posicao.quantity),
                "average_cost": numero(posicao.average_cost),
                "current_price": numero(preco) if preco is not None else None,
                "market_value": dinheiro(sinal * posicao.quantity * preco) if preco is not None else None,
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
                    "price": numero(quote.last_price),
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
                    "price": numero(point.price),
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
                "amount": dinheiro(item.amount),
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
            "generated_at": gerado_em.isoformat(),
            "gerado_em": gerado_em.isoformat(),
            "data_de_referencia": hoje.isoformat(),
            "high_watermark": watermark,
            "capacidades": {
                "holdings": True,
                "position_ledger": True,
                "income": True,
                "activities": True,
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
                "activities": {
                    "available": True,
                    "endpoint": "/patrimonio/v4/activities",
                    "scope": "resumos de transações encerradas e proventos persistidos",
                    "trade_details": False,
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
