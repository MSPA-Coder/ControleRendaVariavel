"""Formato regional (Brasil ou EUA) de apresentação, por usuário.

Só UX: decide como datas e números aparecem e são digitados na tela. O que o
servidor recebe, grava, importa ou exporta não passa por aqui -- continua ISO
(``AAAA-MM-DD``) e decimal com ponto (``1234.56``), como sempre foi.

O formato do usuário da requisição fica numa ``ContextVar`` preenchida por
``app.activate_regional_format``. Fora de uma requisição (CLI, coletor, teste de
serviço) vale o padrão Brasil, que é o que o sistema sempre mostrou.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime
from typing import Final

REGIONAL_FORMAT_BR: Final = "br"
REGIONAL_FORMAT_US: Final = "us"
DEFAULT_REGIONAL_FORMAT: Final = REGIONAL_FORMAT_BR
VALID_REGIONAL_FORMATS: Final = (REGIONAL_FORMAT_BR, REGIONAL_FORMAT_US)
REGIONAL_FORMAT_LABELS: Final = {
    REGIONAL_FORMAT_BR: "Brasil",
    REGIONAL_FORMAT_US: "EUA",
}
REGIONAL_FORMAT_EXAMPLES: Final = {
    REGIONAL_FORMAT_BR: "Data 31/12/2026 · Valor 1.234,56",
    REGIONAL_FORMAT_US: "Data 12/31/2026 · Valor 1,234.56",
}

_formato: ContextVar[str] = ContextVar("formato_regional", default=DEFAULT_REGIONAL_FORMAT)

# Padrões no vocabulário do ``strftime``. O curto (``dd-Mmm-aa``) tem o mês por
# extenso abreviado, então só a ordem muda.
_PADRAO_DATA = {REGIONAL_FORMAT_BR: "%d/%m/%Y", REGIONAL_FORMAT_US: "%m/%d/%Y"}
_PADRAO_DATA_CURTA = {REGIONAL_FORMAT_BR: "%d-%b-%y", REGIONAL_FORMAT_US: "%b-%d-%y"}
_PADRAO_DIA_MES = {REGIONAL_FORMAT_BR: "%d/%m", REGIONAL_FORMAT_US: "%m/%d"}
_PADRAO_DIA_MES_ANO2 = {REGIONAL_FORMAT_BR: "%d/%m/%y", REGIONAL_FORMAT_US: "%m/%d/%y"}


def normalize_regional_format(value: str | None) -> str:
    return value if value in VALID_REGIONAL_FORMATS else DEFAULT_REGIONAL_FORMAT


def formato_ativo() -> str:
    return _formato.get()


def ativar(formato: str | None):
    """Ativa o formato e devolve o token para ``desativar``."""
    return _formato.set(normalize_regional_format(formato))


def desativar(token) -> None:
    _formato.reset(token)


def _eua() -> bool:
    return _formato.get() == REGIONAL_FORMAT_US


def formatar_data(valor: date | datetime | None, *, ausente: str = "") -> str:
    if not valor:
        return ausente
    return valor.strftime(_PADRAO_DATA[_formato.get()])


def formatar_data_curta(valor: date | datetime | None, *, ausente: str = "") -> str:
    if not valor:
        return ausente
    return valor.strftime(_PADRAO_DATA_CURTA[_formato.get()])


def formatar_data_hora(valor: datetime | None, *, segundos: bool = False, ausente: str = "") -> str:
    if not valor:
        return ausente
    hora = "%H:%M:%S" if segundos else "%H:%M"
    return valor.strftime(f"{_PADRAO_DATA[_formato.get()]} {hora}")


def formatar_dia_mes(valor: date | datetime | None, *, ausente: str = "") -> str:
    if not valor:
        return ausente
    return valor.strftime(_PADRAO_DIA_MES[_formato.get()])


def formatar_dia_mes_ano2(valor: date | datetime | None, *, ausente: str = "") -> str:
    if not valor:
        return ausente
    return valor.strftime(_PADRAO_DIA_MES_ANO2[_formato.get()])


def adaptar_numero(texto: str) -> str:
    """Troca os separadores de um número já formatado no padrão brasileiro.

    ``1.234,56`` vira ``1,234.56`` no formato EUA. O marcador ``\\x00`` evita
    passar duas vezes pelo mesmo caractere (o mesmo cuidado de
    ``sharedauth.formatting``).
    """
    if not _eua():
        return texto
    return texto.replace(".", "\x00").replace(",", ".").replace("\x00", ",")
