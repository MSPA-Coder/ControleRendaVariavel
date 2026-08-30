"""O papel decide, e a decisao fica no servidor.

Esconder o menu e apresentacao. O que impede um operador de alterar o coletor e
o `@requer_admin` na rota -- e e isso que este arquivo mede, sem passar pela
interface.
"""

from __future__ import annotations

import pytest
from flask import abort

from app.authorization import PAPEL_ADMIN, requer_admin
from app.models import ROLE_ADMIN, ROLE_OPERADOR, VALID_ROLES, User


class _UsuarioFalso:
    """Substitui `current_user` sem tocar o banco: o decorator so consulta
    `is_admin`, entao um duplo com essa propriedade exercita a decisao real."""

    def __init__(self, papel: str) -> None:
        self.role = papel

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


@pytest.fixture
def rota_protegida():
    @requer_admin
    def view():
        return "alcancou"

    return view


def test_admin_alcanca(app, monkeypatch, rota_protegida):
    monkeypatch.setattr("app.authorization.current_user", _UsuarioFalso(ROLE_ADMIN))
    with app.test_request_context("/settings"):
        assert rota_protegida() == "alcancou"


def test_operador_recebe_403(app, monkeypatch, rota_protegida):
    monkeypatch.setattr("app.authorization.current_user", _UsuarioFalso(ROLE_OPERADOR))
    with app.test_request_context("/settings"), pytest.raises(Exception) as erro:
        rota_protegida()
    assert "403" in str(erro.value)


def test_anonimo_sem_papel_recebe_403(app, monkeypatch, rota_protegida):
    # `getattr(current_user, "is_admin", False)` precisa negar quando a
    # propriedade nem existe, nao estourar.
    monkeypatch.setattr("app.authorization.current_user", object())
    with app.test_request_context("/settings"), pytest.raises(Exception) as erro:
        rota_protegida()
    assert "403" in str(erro.value)


def test_papel_padrao_do_modelo_e_o_menos_privilegiado():
    # Esquecer o decorator em uma rota nova nao pode combinar com um padrao
    # permissivo: o padrao precisa ser `operador`.
    coluna = User.__table__.columns["role"]
    assert coluna.default.arg == ROLE_OPERADOR
    assert coluna.server_default.arg == ROLE_OPERADOR


def test_papeis_validos_sao_apenas_dois():
    assert {ROLE_ADMIN, ROLE_OPERADOR} == VALID_ROLES


#: Endpoints restritos a administradores.
#:
#: Sao as duas telas do grupo "Sistema" no menu (Configuracoes e Usuarios) mais
#: as duas rotas que so elas acionam: o controle do coletor RTD e o pedido de
#: atualizacao imediata.
ENDPOINTS_DE_ADMIN = frozenset(
    {
        "portfolio.settings",
        "portfolio.request_collector_refresh",
        "portfolio.rtd_service_partial",
        "users.index",
        "users.create",
        "users.edit",
        "users.reset_user_password",
        "users.change_active",
    }
)


def _endpoints_protegidos_por_papel(app) -> set[str]:
    return {
        endpoint
        for endpoint, view in app.view_functions.items()
        if getattr(view, "papel_exigido", None) == PAPEL_ADMIN
    }


def test_conjunto_de_rotas_de_admin_e_exatamente_o_declarado(app):
    """Compara o CONJUNTO, nao uma rota de cada vez.

    A versao anterior deste teste olhava so `/settings`, e olhava a coisa
    errada: `getattr(view, "__wrapped__")` e verdadeiro para qualquer decorator
    que use `functools.wraps`, inclusive um `@login_required` sozinho. Passava
    sem provar papel nenhum.

    Verificar rota por rota tambem so encontra o que alguem ja suspeitava.
    Comparar o conjunto encontra a rota que ninguem lembrou de verificar --
    que foi como o Dashboard, as Projecoes e a Posicao por conta ficaram sem
    verificacao no projeto irmao.
    """
    assert _endpoints_protegidos_por_papel(app) == ENDPOINTS_DE_ADMIN, (
        "As rotas protegidas por papel divergiram da lista declarada. Se uma "
        "rota nova e mesmo de administrador, acrescente-a aqui; se um "
        "decorator sumiu, devolva-o -- nao ajuste a lista para o teste passar."
    )


def test_existem_rotas_de_admin_para_verificar(app):
    # Protege o proprio teste: sem isto, um erro que zerasse a coleta deixaria
    # a comparacao acima passando por vacuidade.
    assert _endpoints_protegidos_por_papel(app)


def test_abort_403_e_o_comportamento_esperado(app):
    # Documenta a escolha: 403, nao redirecionar para o login. Quem chegou aqui
    # ja esta autenticado.
    with app.test_request_context("/"), pytest.raises(Exception) as erro:
        abort(403)
    assert "403" in str(erro.value)
