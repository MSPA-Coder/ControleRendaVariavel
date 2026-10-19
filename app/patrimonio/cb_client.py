"""Cliente somente leitura do Controle Bancário, pelo contrato ``patrimonio/v4``.

O CB é dono de todo movimento de dinheiro, inclusive o das contas de corretora;
o CRV só o **lê**, para conferir a liquidação de cada pregão contra as execuções
importadas do myProfit. Nada daqui escreve no CB, e o token do consumidor (o
``PATRIMONIO_INTEGRATION_TOKEN`` do CB, copiado para um segredo de arquivo deste
projeto) nunca entra em log, mensagem de erro ou resposta.

Sem URL e token configurados a função devolve ``None`` e a tela mostra "CB não
configurado": a conferência é um recurso opcional e não pode derrubar o resto.

O contrato publica tudo em um snapshot (contas, categorias e lançamentos). Este
cliente o lê inteiro e filtra: os lançamentos da categoria "Operações em
Bolsa", em ordem de data de pregão (``read_settlements``), e os lançamentos de
renda que o CB marcou com ``income_kind``/``income_role`` (``read_income_entries``),
que alimentam os proventos de 2026 em diante do CRV.

TRANSIÇÃO (CB-1): o CB está acrescentando ao ``cash_entry`` o campo opcional
``settlement`` (``pregao_date`` e ``note``). Enquanto ele não está em produção, o
pregão sai da descrição do lançamento por :func:`pregao_da_descricao`. Quando o
campo oficial chegar, ele tem precedência e a função (e o ramo que a chama) pode
ser removida.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

#: Nome da categoria de movimentação que o CB usa para a liquidação do pregão.
#: O CB já teve grafia errada em outra categoria ("Açoes"), então a comparação
#: ignora acento e caixa.
SETTLEMENT_CATEGORY = "Operações em Bolsa"
TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

Fetcher = Callable[[str, str], Any]


class CbUnavailableError(RuntimeError):
    """O CB não respondeu ou respondeu algo que não é o contrato esperado.

    A mensagem é segura para a tela: nunca contém o token nem o corpo recebido.
    """


@dataclass(frozen=True, slots=True)
class CbConfig:
    base_url: str
    token: str = ""

    def __repr__(self) -> str:  # o token não pode vazar por um repr em log
        return f"CbConfig(base_url={self.base_url!r}, token=<oculto>)"


@dataclass(frozen=True, slots=True)
class CbSettlement:
    """Uma liquidação de pregão lançada no CB, com o valor líquido assinado."""

    entry_id: str
    institution: str
    account_name: str
    currency: str
    pregao_date: date | None
    amount: Decimal
    status: str
    description: str


@dataclass(frozen=True, slots=True)
class CbSettlements:
    items: list[CbSettlement]
    #: Lançamentos da categoria cujo pregão não saiu nem do campo oficial nem
    #: da descrição. Ficam fora da conferência, mas contados e mostrados.
    without_pregao: list[CbSettlement]
    #: Liquidação e ajuste de BM&F (futuros): mesma categoria no CB, mas o CRV
    #: não trata futuros, então ficam fora da conferência, contados.
    futures: list[CbSettlement] = field(default_factory=list)
    #: (instituição, moeda) -> último dia que o extrato importado cobre, pelo
    #: ``statement_covered_until`` opcional das contas (CB desde 19/10/2026).
    #: Instituição sem o campo não aparece.
    coverage: dict[tuple[str, str], date] = field(default_factory=dict)


_PREGAO_NA_DESCRICAO = re.compile(r"\bPr\s+(\d{2})/(\d{2})/(\d{4})\b")


def pregao_da_descricao(descricao: str | None) -> date | None:
    """Data do pregão na descrição da liquidação do extrato.

    ``"Operações Bolsa D+2 Pr 27/01/2026 Nc. 543216"`` -> ``date(2026, 1, 27)``.
    Devolve ``None`` quando a descrição não tem a data ou ela não existe.

    Função pura e isolada de propósito: é o desvio temporário que o campo
    ``settlement.pregao_date`` do CB-1 torna desnecessário.
    """
    achado = _PREGAO_NA_DESCRICAO.search(descricao or "")
    if achado is None:
        return None
    dia, mes, ano = (int(parte) for parte in achado.groups())
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def is_futures_settlement(descricao: str | None) -> bool:
    """Liquidação ou ajuste de BM&F ("Operações Bm&F Pr ...", "Ajuste Day Trade Bm&F ...").

    Na mesma categoria das ações, mas é o mercado de futuros, fora do escopo do
    CRV; sem isto o ajuste de day trade viraria "só no CB" a cada mini-contrato.
    """
    return "bm&f" in (descricao or "").casefold()


def _fold(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", " ".join((value or "").split()))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def institution_identity(name: str | None) -> str:
    """Mesma regra de ``identidade()`` do CB: minúsculas, sem acento, hifenizado."""
    return re.sub(r"[^a-z0-9]+", "-", _fold(name)).strip("-")


def parse_config(base_url: str | None, token: str | None) -> CbConfig | None:
    """Configuração do CB, ou ``None`` quando falta URL ou token."""
    base = (base_url or "").strip().rstrip("/")
    secret = (token or "").strip()
    if not base or not secret:
        return None
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise CbUnavailableError("CB_PATRIMONIO_URL deve ser um endereço http(s).")
    return CbConfig(base_url=base, token=secret)


def _fetch_json(url: str, token: str) -> Any:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if urlsplit(url).scheme == "http":
        # O CB redireciona http para https (USE_HTTPS) e confia no cabeçalho do
        # proxy. O salto interno pela rede do Compose não passa pelo Nginx, então
        # declara aqui o que o proxy declararia, em vez de exigir TLS entre contêineres.
        headers["X-Forwarded-Proto"] = "https"
    request = Request(url, headers=headers)  # noqa: S310 - esquema validado em parse_config
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise CbUnavailableError("O CB recusou o token de consumidor.") from exc
        if exc.code == 503:
            raise CbUnavailableError("A integração de patrimônio não está configurada no CB.") from exc
        raise CbUnavailableError(f"O CB respondeu HTTP {exc.code}.") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise CbUnavailableError("Não foi possível alcançar o CB.") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise CbUnavailableError("A resposta do CB excede o limite aceito.")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise CbUnavailableError("A resposta do CB não é JSON válido.") from exc


def _amount(payload: dict[str, Any]) -> Decimal:
    raw = payload.get("realized_amount")
    if raw is None:
        raw = payload.get("planned_amount")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise CbUnavailableError("Valor de lançamento inválido na resposta do CB.") from exc
    if not value.is_finite():
        raise CbUnavailableError("Valor de lançamento inválido na resposta do CB.")
    return -abs(value) if payload.get("entry_type") == "despesa" else abs(value)


def _pregao(payload: dict[str, Any]) -> date | None:
    settlement = payload.get("settlement")
    if isinstance(settlement, dict) and settlement.get("pregao_date"):
        try:
            return date.fromisoformat(str(settlement["pregao_date"]))
        except ValueError:
            return None
    return pregao_da_descricao(payload.get("description"))


def _read_snapshot(
    config: CbConfig, fetcher: Fetcher | None
) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    """Lê o snapshot v4 e separa contas, categorias (id -> nome) e lançamentos."""
    fetch = fetcher or _fetch_json
    document = fetch(f"{config.base_url}/snapshot", config.token)
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise CbUnavailableError("A resposta do CB não segue o contrato patrimonio/v4.")

    accounts: dict[str, dict[str, Any]] = {}
    categories: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    for item in document["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("payload"), dict):
            continue
        payload = item["payload"]
        if item.get("resource") == "account":
            accounts[str(payload.get("source_id"))] = payload
        elif item.get("resource") == "category":
            categories[str(payload.get("source_id"))] = str(payload.get("name") or "")
        elif item.get("resource") == "cash_entry":
            entries.append(payload)
    return accounts, categories, entries


def read_settlements(config: CbConfig, *, fetcher: Fetcher | None = None) -> CbSettlements:
    """Lê o snapshot v4 e devolve as liquidações de pregão, só leitura.

    ``fetcher(url, token)`` existe para os testes trocarem a rede; em produção
    é :func:`_fetch_json`.
    """
    accounts, categories, entries = _read_snapshot(config, fetcher)

    wanted = _fold(SETTLEMENT_CATEGORY)
    settlements: list[CbSettlement] = []
    for payload in entries:
        if _fold(categories.get(str(payload.get("category_id")))) != wanted:
            continue
        account = accounts.get(str(payload.get("account_id")), {})
        settlements.append(
            CbSettlement(
                entry_id=str(payload.get("source_id")),
                institution=str(account.get("institution") or institution_identity(account.get("institution_name"))),
                account_name=str(account.get("name") or ""),
                currency=str(payload.get("currency") or account.get("currency") or ""),
                pregao_date=_pregao(payload),
                amount=_amount(payload),
                status=str(payload.get("status") or ""),
                description=str(payload.get("description") or ""),
            )
        )
    futures = [item for item in settlements if is_futures_settlement(item.description)]
    stocks = [item for item in settlements if not is_futures_settlement(item.description)]
    return CbSettlements(
        items=[item for item in stocks if item.pregao_date is not None],
        without_pregao=[item for item in stocks if item.pregao_date is None],
        futures=futures,
        coverage=statement_coverage(accounts),
    )


def statement_coverage(accounts: dict[str, dict[str, Any]]) -> dict[tuple[str, str], date]:
    """Último dia coberto pelo extrato, por (instituição, moeda); o maior entre as contas."""
    coverage: dict[tuple[str, str], date] = {}
    for account in accounts.values():
        raw = account.get("statement_covered_until")
        if not raw:
            continue
        try:
            covered = date.fromisoformat(str(raw))
        except ValueError:
            continue
        key = (
            str(account.get("institution") or institution_identity(account.get("institution_name"))),
            str(account.get("currency") or ""),
        )
        if key not in coverage or covered > coverage[key]:
            coverage[key] = covered
    return coverage


@dataclass(frozen=True, slots=True)
class CbIncomeEntry:
    """Um lançamento de renda (crédito, retenção ou estorno) das contas de corretora.

    ``amount`` é sempre o módulo; ``flow`` diz se entrou (``receita``) ou saiu
    (``despesa``) da conta. ``instrument``, ``kind`` e ``role`` são os campos
    opcionais do contrato v4 (CB-1) e podem faltar.
    """

    entry_id: str
    account_id: str
    institution: str
    account_name: str
    currency: str
    realized_date: date
    amount: Decimal
    flow: str
    instrument: str | None
    kind: str | None
    role: str | None
    description: str


@dataclass(frozen=True, slots=True)
class CbIncome:
    entries: list[CbIncomeEntry]
    #: Lançamentos de renda ainda não realizados ou sem data realizada: ficam
    #: fora, mas contados, porque só o dinheiro que já entrou vira provento.
    not_realized: int = 0


def read_income_entries(config: CbConfig, *, fetcher: Fetcher | None = None) -> CbIncome:
    """Lançamentos com ``income_role`` ou ``income_kind``, só leitura.

    A categoria não é consultada: o CB-1 marca cada lançamento de renda, e a
    categoria tem erro de grafia histórico ("Açoes"). Quem não foi marcado não é
    renda para o CRV.
    """
    accounts, _categories, entries = _read_snapshot(config, fetcher)
    result: list[CbIncomeEntry] = []
    not_realized = 0
    for payload in entries:
        role = payload.get("income_role")
        kind = payload.get("income_kind")
        if not role and not kind:
            continue
        realized = payload.get("realized_date")
        if payload.get("status") != "realizado" or not realized:
            not_realized += 1
            continue
        try:
            realized_date = date.fromisoformat(str(realized))
        except ValueError:
            not_realized += 1
            continue
        account = accounts.get(str(payload.get("account_id")), {})
        result.append(
            CbIncomeEntry(
                entry_id=str(payload.get("source_id")),
                account_id=str(payload.get("account_id")),
                institution=str(account.get("institution") or institution_identity(account.get("institution_name"))),
                account_name=str(account.get("name") or ""),
                currency=str(payload.get("currency") or account.get("currency") or ""),
                realized_date=realized_date,
                amount=abs(_amount(payload)),
                flow="despesa" if payload.get("entry_type") == "despesa" else "receita",
                instrument=str(payload["instrument"]).upper() if payload.get("instrument") else None,
                kind=str(kind) if kind else None,
                role=str(role) if role else None,
                description=str(payload.get("description") or ""),
            )
        )
    return CbIncome(entries=result, not_realized=not_realized)
