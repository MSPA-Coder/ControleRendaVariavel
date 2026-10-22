"""Validação de entrada que não precisa de banco: faixa dos números, texto e ticker."""

from decimal import Decimal

import pytest

from app.accounts.users import UserManagementError, _recusar_senha_fraca
from app.core.validation import LIMITE_MAXIMO, limitar_texto, parse_finite_decimal
from app.quotes.reference_data import parse_ticker


@pytest.mark.parametrize("valor", ["1e30", "-1e30", "1000000000000", "99999999999999999999"])
def test_numero_acima_do_teto_e_recusado(valor):
    with pytest.raises(ValueError, match="menor que"):
        parse_finite_decimal(valor, field_name="uma quantidade")


@pytest.mark.parametrize("valor", ["0.000000001", "1e-30", "-0.000000001"])
def test_numero_menor_que_a_precisao_da_coluna_e_recusado(valor):
    with pytest.raises(ValueError, match="8 casas"):
        parse_finite_decimal(valor, field_name="uma quantidade")


@pytest.mark.parametrize("valor", ["0", "0.00000001", "12.5", str(LIMITE_MAXIMO), "-3"])
def test_numero_dentro_da_faixa_passa(valor):
    assert parse_finite_decimal(valor, field_name="um valor") == Decimal(valor)


@pytest.mark.parametrize("valor", ["NaN", "Infinity", "-Infinity", "abc", ""])
def test_nao_numero_continua_recusado(valor):
    with pytest.raises(ValueError):
        parse_finite_decimal(valor, field_name="um valor")


def test_texto_livre_respeita_o_tamanho_da_coluna():
    assert limitar_texto("  ok  ", field_name="a nota", maximo=5) == "ok"
    assert limitar_texto(None, field_name="a nota", maximo=5) == ""
    with pytest.raises(ValueError, match="no máximo 5"):
        limitar_texto("123456", field_name="a nota", maximo=5)


def _ticker(**campos):
    form = {"symbol": "ABCD3", "trading_name": "Ativo", "market": "B3",
            "rtd_market_code": "B", "currency": "BRL", **campos}
    return parse_ticker(form)


@pytest.mark.parametrize("simbolo", ["A B;--", "ab c", "A'B", "AB<3>", "-ABC", "ABC 3", "AB\"C"])
def test_simbolo_com_espaco_ou_pontuacao_estranha_e_recusado(simbolo):
    with pytest.raises(ValueError, match="só letras, números"):
        _ticker(symbol=simbolo)


@pytest.mark.parametrize("simbolo", ["BRK.B", "BRK-B", "usdbrl=x", "^BVSP", "HODL11", "RAIZH150"])
def test_simbolos_dos_provedores_passam(simbolo):
    assert _ticker(symbol=simbolo, is_benchmark="on").symbol == simbolo.upper()


@pytest.mark.parametrize(
    "mercado,moeda",
    [("NYSE", "BRL"), ("NASDAQ", "BRL"), ("B3", "USD")],
)
def test_mercado_e_moeda_incoerentes_sao_recusados(mercado, moeda):
    with pytest.raises(ValueError, match="é cotado em"):
        _ticker(market=mercado, currency=moeda)


@pytest.mark.parametrize("mercado,moeda", [("B3", "BRL"), ("NYSE", "USD"), ("NASDAQ", "USD")])
def test_mercado_e_moeda_coerentes_passam(mercado, moeda):
    assert _ticker(market=mercado, currency=moeda).currency == moeda


def test_referencia_de_mercado_nao_precisa_casar_mercado_e_moeda():
    assert _ticker(symbol="USDBRL=X", market="NYSE", currency="BRL", is_benchmark="on").is_benchmark


@pytest.mark.parametrize(
    "senha,usuario,trecho",
    [
        ("1234567890", "ana", "só números"),
        ("00000000000", "ana", "só números"),
        ("abababababab", "ana", "repetir"),
        ("ana-ana-ana", "ana", None),
        ("maria-senha-1", "maria", "nome"),
        ("MARIA", "maria", "nome"),
    ],
)
def test_regras_de_senha_fraca(senha, usuario, trecho):
    if trecho is None:
        _recusar_senha_fraca(senha, usuario)  # nome curto (< 4) não bloqueia por conter
        return
    with pytest.raises(UserManagementError, match=trecho):
        _recusar_senha_fraca(senha, usuario)


def test_senha_longa_e_variada_passa():
    _recusar_senha_fraca("cavalo-bateria-grampo-9", "maria")
