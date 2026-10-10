"""Formato regional (Brasil/EUA) por usuário: só apresentação.

Risco que protege: o usuário escolher EUA e ver número ou data em formato
trocado pela metade, ou a escolha de um usuário vazar para a requisição
seguinte. O que é gravado, importado e exportado não passa por esta camada e
não deve mudar.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app import db
from app.core import regional
from app.core.regional import REGIONAL_FORMAT_BR, REGIONAL_FORMAT_US, normalize_regional_format
from app.models import User, UserPreference

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def eua():
    token = regional.ativar(REGIONAL_FORMAT_US)
    yield
    regional.desativar(token)


@pytest.fixture
def contexto(app):
    """`mask_value` lê a sessão: os filtros precisam de uma requisição."""
    with app.test_request_context("/"):
        yield


def _filtro(app, nome):
    return app.jinja_env.filters[nome]


def test_sem_requisicao_vale_o_formato_do_brasil():
    assert regional.formato_ativo() == REGIONAL_FORMAT_BR
    assert regional.formatar_data(date(2026, 12, 31)) == "31/12/2026"
    assert regional.formatar_data_hora(datetime(2026, 12, 31, 8, 5, 9), segundos=True) == "31/12/2026 08:05:09"


def test_formato_desconhecido_cai_no_brasil():
    assert normalize_regional_format("xx") == REGIONAL_FORMAT_BR
    assert normalize_regional_format(None) == REGIONAL_FORMAT_BR


def test_datas_no_formato_dos_eua(eua):
    assert regional.formatar_data(date(2026, 12, 31)) == "12/31/2026"
    assert regional.formatar_data(date(2026, 3, 4)) == "03/04/2026"
    assert regional.formatar_dia_mes(date(2026, 12, 31)) == "12/31"
    assert regional.formatar_dia_mes_ano2(date(2026, 12, 31)) == "12/31/26"
    assert regional.formatar_data_curta(date(2026, 12, 31)) == "Dec-31-26"


def test_data_ausente_nao_vira_texto():
    assert regional.formatar_data(None) == ""
    assert regional.formatar_data_curta(None) == ""


def test_numeros_no_brasil_continuam_iguais(app, contexto):
    assert _filtro(app, "money")(Decimal("1234.5")) == "R$ 1.234,50"
    assert _filtro(app, "currency")(Decimal("1234.5"), "USD") == "US$ 1.234,50"
    assert _filtro(app, "percent")(Decimal("0.1234")) == "12,3%"


def test_numeros_nos_eua_trocam_so_os_separadores_e_mantem_o_simbolo(app, contexto, eua):
    assert _filtro(app, "money")(Decimal("1234567.5")) == "R$ 1,234,567.50"
    assert _filtro(app, "currency")(Decimal("1234.5"), "USD") == "US$ 1,234.50"
    assert _filtro(app, "currency")(Decimal("1234.00"), "BRL", 2, True) == "R$ 1,234"
    assert _filtro(app, "number")(Decimal("-1234.567"), 3) == "-1,234.567"
    assert _filtro(app, "percent")(Decimal("0.1234")) == "12.3%"
    assert _filtro(app, "quantity")(Decimal("1000"), "BRL") == "1,000"


def test_filtros_de_data_seguem_o_formato_ativo(app, contexto, eua):
    assert _filtro(app, "udate")(date(2026, 12, 31)) == "12/31/2026"
    assert _filtro(app, "ushort")(date(2026, 12, 31)) == "Dec-31-26"
    assert _filtro(app, "uday_month")(date(2026, 12, 31)) == "12/31"
    assert _filtro(app, "read_at")("2026-12-31T15:00:00+00:00").startswith("12/31/2026 ")


@pytest.mark.banco
class TestPreferenciaDoUsuario:
    @pytest.fixture
    def cenario(self, app_com_banco):
        with app_com_banco.app_context():
            sufixo = uuid4().hex
            usuarios = [User(username=f"regional-{sufixo}-{i}", role="operador") for i in range(2)]
            for usuario in usuarios:
                usuario.set_password("Synthetic-regional-only-2026!")
            db.session.add_all(usuarios)
            db.session.commit()
            ids = [u.id for u in usuarios]
            sessoes = [u.get_id() for u in usuarios]
            db.session.add_all([UserPreference(user_id=i) for i in ids])
            db.session.commit()

        def cliente(indice):
            c = app_com_banco.test_client()
            proprio = uuid4().hex
            c.environ_base["REMOTE_ADDR"] = "2001:db8::" + ":".join(proprio[j:j + 4] for j in range(0, 16, 4))
            pagina = c.get("/login").get_data(as_text=True)
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', pagina).group(1)
            with c.session_transaction() as sessao:
                sessao["_user_id"] = sessoes[indice]
                sessao["_fresh"] = True
            return c, csrf

        try:
            yield app_com_banco, cliente, ids
        finally:
            with app_com_banco.app_context():
                db.session.execute(delete(UserPreference).where(UserPreference.user_id.in_(ids)))
                db.session.execute(delete(User).where(User.id.in_(ids)))
                db.session.commit()

    def test_padrao_e_brasil_para_quem_ja_existia(self, cenario):
        app, _, ids = cenario
        with app.app_context():
            assert db.session.get(UserPreference, ids[0]).regional_format == REGIONAL_FORMAT_BR

    def test_salva_a_escolha_so_da_propria_conta_e_recusa_valor_estranho(self, cenario):
        app, cliente, ids = cenario
        c, csrf = cliente(0)
        dados = {"csrf_token": csrf, "theme": "light", "risk_free_rate_annual": "0.1",
                 "benchmark_ticker_id": "", "regional_format": "us"}
        assert c.post("/preferences", data=dados).status_code == 302
        with app.app_context():
            assert db.session.get(UserPreference, ids[0]).regional_format == REGIONAL_FORMAT_US
            assert db.session.get(UserPreference, ids[1]).regional_format == REGIONAL_FORMAT_BR

        assert c.post("/preferences", data={**dados, "regional_format": "../x"}).status_code == 302
        with app.app_context():
            assert db.session.get(UserPreference, ids[0]).regional_format == REGIONAL_FORMAT_BR

    def test_cada_requisicao_usa_o_formato_do_seu_usuario(self, cenario):
        app, cliente, ids = cenario
        c1, csrf = cliente(0)
        c1.post("/preferences", data={"csrf_token": csrf, "theme": "light", "risk_free_rate_annual": "0.1",
                                      "benchmark_ticker_id": "", "regional_format": "us"})
        pagina_eua = c1.get("/preferences").get_data(as_text=True)
        assert 'data-regional="us"' in pagina_eua
        assert 'name="regional_format" value="us" aria-label="Formato EUA" checked' in pagina_eua

        c2, _ = cliente(1)
        assert 'data-regional="br"' in c2.get("/preferences").get_data(as_text=True)
        assert regional.formato_ativo() == REGIONAL_FORMAT_BR
