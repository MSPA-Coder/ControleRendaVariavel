"""Quais linhas de planilha o CB passou a cobrir (pareamento puro).

Em reais, Genial e XP, as linhas de planilha com pagamento a partir do corte
(``PROVENTOS_CB_CORTE``) seriam contadas EM DOBRO com as linhas do CB. Antes de
marcar qualquer uma como substituida, cada linha de planilha precisa ter um
correspondente no CB ou ficar listada como divergencia. Este modulo so decide o
pareamento; quem le o banco e grava a marca e ``app.dividends.cb_sync``.

Como a planilha e o CB diferem (estudo de 18/10/2026):

* dividendo e JCP batem ao centavo, na mesma data;
* a planilha e o myProfit mostram o aluguel em 57,5% do credito bruto
  (``RENT_SHEET_FACTOR``); o caixa do CB recebe menos, pelas retencoes;
* a data da planilha vem 0 a 3 dias antes da data do caixa (a janela aqui e de
  ``DATE_WINDOW_DAYS``) e nenhum par cruza a virada de mes;
* um credito do CB pode virar duas ou tres linhas na planilha.

O pareamento e por (corretora, ativo, tipo): cada credito do CB, em ordem de
data, reclama as linhas livres da janela cuja soma e a esperada (dentro da
tolerancia); entre as combinacoes possiveis vence a de menor diferenca. Linha
que nenhum credito reclama fica em ``unmatched_sheet`` -- a divergencia que o
mantenedor precisa ver -- e credito sem linha fica em ``unmatched_cb``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import combinations

CENT = Decimal("0.01")
RENT_SHEET_FACTOR = Decimal("0.575")
DATE_WINDOW_DAYS = 5
MAX_ROWS_PER_CREDIT = 4
#: Tolerancia de arredondamento: um centavo e meio por linha da planilha.
ROW_TOLERANCE = Decimal("0.015")
BASE_TOLERANCE = Decimal("0.02")


@dataclass(frozen=True, slots=True)
class SheetLine:
    id: int
    broker: str
    ticker: str
    kind: str
    payment_date: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CbLine:
    id: int
    broker: str
    ticker: str
    kind: str
    payment_date: date
    gross: Decimal
    net: Decimal


@dataclass(frozen=True, slots=True)
class Pair:
    cb: CbLine
    sheet: tuple[SheetLine, ...]
    expected: Decimal

    @property
    def sheet_total(self) -> Decimal:
        return sum((line.amount for line in self.sheet), Decimal("0"))

    @property
    def difference(self) -> Decimal:
        return self.sheet_total - self.expected


@dataclass(slots=True)
class MatchResult:
    pairs: list[Pair] = field(default_factory=list)
    unmatched_sheet: list[SheetLine] = field(default_factory=list)
    unmatched_cb: list[CbLine] = field(default_factory=list)

    @property
    def matched_sheet_ids(self) -> list[int]:
        return [line.id for pair in self.pairs for line in pair.sheet]


def expected_sheet_amount(cb: CbLine) -> Decimal:
    """O que a planilha mostra para este credito do CB."""
    if cb.kind == "aluguel":
        return (cb.gross * RENT_SHEET_FACTOR).quantize(CENT, rounding=ROUND_HALF_EVEN)
    return cb.gross


def _tolerance(rows: int) -> Decimal:
    return BASE_TOLERANCE + ROW_TOLERANCE * rows


def match_lines(sheet: Iterable[SheetLine], cb: Iterable[CbLine]) -> MatchResult:
    result = MatchResult()
    sheet_groups: dict[tuple[str, str, str], list[SheetLine]] = defaultdict(list)
    cb_groups: dict[tuple[str, str, str], list[CbLine]] = defaultdict(list)
    for line in sheet:
        sheet_groups[(line.broker, line.ticker, line.kind)].append(line)
    for credit in cb:
        cb_groups[(credit.broker, credit.ticker, credit.kind)].append(credit)

    for key in sorted(sheet_groups.keys() | cb_groups.keys()):
        free = sorted(sheet_groups.get(key, []), key=lambda item: (item.payment_date, item.id))
        for credit in sorted(cb_groups.get(key, []), key=lambda item: (item.payment_date, item.id)):
            expected = expected_sheet_amount(credit)
            window_start = credit.payment_date - timedelta(days=DATE_WINDOW_DAYS)
            candidates = [line for line in free if window_start <= line.payment_date <= credit.payment_date]
            best: tuple[Decimal, int, tuple[SheetLine, ...]] | None = None
            for size in range(1, min(MAX_ROWS_PER_CREDIT, len(candidates)) + 1):
                for subset in combinations(candidates, size):
                    diff = abs(sum((line.amount for line in subset), Decimal("0")) - expected)
                    if diff > _tolerance(size):
                        continue
                    option = (diff, size, subset)
                    if best is None or option[:2] < best[:2]:
                        best = option
            if best is None:
                result.unmatched_cb.append(credit)
                continue
            claimed = best[2]
            free = [line for line in free if line not in claimed]
            result.pairs.append(Pair(cb=credit, sheet=claimed, expected=expected))
        result.unmatched_sheet.extend(free)
    result.pairs.sort(key=lambda pair: (pair.cb.payment_date, pair.cb.id))
    result.unmatched_sheet.sort(key=lambda line: (line.payment_date, line.id))
    result.unmatched_cb.sort(key=lambda credit: (credit.payment_date, credit.id))
    return result
