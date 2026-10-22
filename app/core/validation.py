from __future__ import annotations

from decimal import Decimal, InvalidOperation

#: Teto aceito em qualquer número digitado. As colunas de valor são
#: ``numeric(24, 8)`` e estouram em 10**16; ficar bem abaixo disso deixa folga
#: para somas e produtos (quantidade x preço) sem chegar ao limite do banco.
LIMITE_MAXIMO = Decimal("999999999999")

#: Menor valor não nulo que as colunas conseguem guardar (8 casas decimais).
#: Abaixo disso o banco arredondaria para zero e a regra de "maior que zero"
#: valeria só na tela.
LIMITE_MINIMO_NAO_NULO = Decimal("0.00000001")


def parse_finite_decimal(raw_value: str, *, field_name: str) -> Decimal:
    """Parse a user-provided decimal while rejecting NaN, infinities and
    magnitudes the numeric columns cannot store."""

    try:
        value = Decimal(raw_value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Informe {field_name} válido.") from exc
    if not value.is_finite():
        raise ValueError(f"Informe {field_name} finito.")
    magnitude = abs(value)
    if magnitude > LIMITE_MAXIMO:
        raise ValueError(f"Informe {field_name} menor que {LIMITE_MAXIMO:,}.".replace(",", "."))
    if value != 0 and magnitude < LIMITE_MINIMO_NAO_NULO:
        raise ValueError(f"Informe {field_name} com no máximo 8 casas decimais.")
    return value


def limitar_texto(raw_value: str | None, *, field_name: str, maximo: int) -> str:
    """Texto livre aparado e dentro do tamanho da coluna.

    O banco recusa o que passa do limite com um erro de integridade; recusar
    aqui devolve a mensagem ao formulário em vez de uma resposta 500.
    """

    texto = (raw_value or "").strip()
    if len(texto) > maximo:
        raise ValueError(f"{field_name.capitalize()} aceita no máximo {maximo} caracteres.")
    return texto
