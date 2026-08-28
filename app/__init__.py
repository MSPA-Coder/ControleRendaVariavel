from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Flask, request, session
from flask_login import LoginManager, current_user  # type: ignore[import-untyped]
from flask_migrate import Migrate  # type: ignore[import-untyped]
from flask_sqlalchemy import SQLAlchemy
from sharedauth.access import requer_login
from sharedauth.config import ler_flag, montar_url_postgres
from sharedauth.csrf import iniciar_csrf
from sharedauth.messages import registrar_mensagens
from sharedauth.ratelimit import LIMITE_LOGIN_PADRAO, aplicar_limite, iniciar_limiter
from sharedauth.security import registrar_cabecalhos
from sharedauth.session import configurar_sessao
from sharedauth.ui import registrar_ui
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix

from app.secret_files import environment_value

if TYPE_CHECKING:
    from app.models import User

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    pass


Base.metadata.naming_convention = convention
db = SQLAlchemy(model_class=Base)
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Faça login para continuar."
login_manager.login_message_category = "error"

# Endpoints reachable without an authenticated session. Kept intentionally
# small: everything else in the app shows personal financial data.
PUBLIC_ENDPOINTS = frozenset({
    "auth.login",
    "portfolio.health",
    "portfolio.collector_agent_configuration",
    "portfolio.collector_agent_quotes",
    "portfolio.collector_agent_failure",
    "static",
    # CSS do banner de `flash()` (ícone por categoria) que login.html usa: a
    # tela de login é a única página fora da sessão que precisa de um
    # estático do sharedauth, e sem isto `requer_login` bloquearia o
    # próprio arquivo que renderiza "Usuário ou senha inválidos." — a
    # mensagem apareceria sem estilo nenhum. `sharedauth_ui.static` (o
    # modal/toast) não entra aqui: só é referenciado em base.html, que já
    # não é servido sem sessão.
    "sharedauth.static",
})

# Telas que exibem o pulso do coletor: a barra do menu em Ações e Cotações, o
# controle em Configurações e os dois fragmentos que o HTMX rebusca.
HEARTBEAT_ENDPOINTS = {
    "portfolio.index",
    "portfolio.quote_history",
    "portfolio.settings",
    "portfolio.collector_heartbeat_partial",
    "portfolio.rtd_service_partial",
}


@login_manager.user_loader  # type: ignore[misc]
def _load_user(user_id: str) -> User | None:
    from app.models import User

    # `int()` sem guarda derruba a requisição com 500 quando o identificador do
    # cookie não é numérico. O cookie é assinado, então não é um caminho de
    # ataque -- mas um cookie de formato antigo vira erro em vez de sessão
    # recusada. Mesma guarda do MegaSena.
    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    return user if user is not None and user.is_active_user else None


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    # `estrito=False` preserva o comportamento deste app: valor irreconhecível
    # cai no padrão em vez de impedir a subida. `FORCE_HTTPS` e
    # `TRUST_PROXY_HEADERS` são propriedades da implantação, e um typo aqui não
    # deve derrubar o serviço -- ele apenas não liga a folga.
    force_https = ler_flag("FORCE_HTTPS", estrito=False)
    configured_secret_key = environment_value("SECRET_KEY")
    configured_database_url = environment_value("DATABASE_URL")
    if not configured_database_url:
        postgres_password = environment_value("POSTGRES_PASSWORD")
        if postgres_password:
            # `montar_url_postgres` substitui o `build_postgres_url` local:
            # mesmo escape com `quote(..., safe="")`, mais validação de porta e
            # de componente vazio, e agora compartilhado com os outros apps.
            configured_database_url = montar_url_postgres(
                usuario=os.getenv("POSTGRES_USER", "investimentos"),
                senha=postgres_password,
                host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
                banco=os.getenv("POSTGRES_DB", "investimentos"),
                porta=os.getenv("POSTGRES_PORT", "5302"),
            )
    app.config.from_mapping(
        SECRET_KEY=configured_secret_key,
        SQLALCHEMY_DATABASE_URI=configured_database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # pool_pre_ping: sem isto, uma conexao que sobrou morta no pool depois
        # de o Postgres reiniciar (deploy, OOM, manutencao) so e descartada
        # quando o SQLAlchemy tenta usa-la de verdade -- e a requisicao que
        # pegou essa conexao leva 500 ate o pool reciclar sozinho. Com o ping,
        # o SQLAlchemy testa a conexao antes de emprestar do pool e troca por
        # uma nova em silencio. Mesma chave usada no MegaSena.
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        RTD_PROG_ID=os.getenv("RTD_PROG_ID", "rtdtrading.rtdserver"),
        RTD_REFRESH_SECONDS=float(os.getenv("RTD_REFRESH_SECONDS", "2")),
        RTD_TIMEOUT_SECONDS=float(os.getenv("RTD_TIMEOUT_SECONDS", "10")),
        RTD_STALE_AFTER_SECONDS=int(os.getenv("RTD_STALE_AFTER_SECONDS", "30")),
        RTD_EXCEL_VISIBLE=ler_flag("RTD_EXCEL_VISIBLE", estrito=False),
        COLLECTOR_AGENT_TOKEN=environment_value("COLLECTOR_AGENT_TOKEN") or "",
        REMOTE_COLLECTOR_ENABLED=ler_flag("REMOTE_COLLECTOR_ENABLED", estrito=False),
        FORCE_HTTPS=force_https,
        TRUST_PROXY_HEADERS=ler_flag("TRUST_PROXY_HEADERS", estrito=False),
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
        RATELIMIT_ENABLED=ler_flag("RATELIMIT_ENABLED", padrao=True, estrito=False),
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        if app.config["TESTING"]:
            app.config["SECRET_KEY"] = "test-only-not-a-real-secret"
        else:
            raise RuntimeError("Defina SECRET_KEY antes de iniciar a aplicação.")
    if not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("Defina DATABASE_URL antes de iniciar a aplicação.")

    configurar_sessao(
        app,
        nome_cookie=os.getenv(
            "RENDA_VARIAVEL_SESSION_COOKIE_NAME",
            "controle_renda_variavel_session",
        ).strip()
        or "controle_renda_variavel_session",
        https_obrigatorio=bool(app.config["FORCE_HTTPS"]),
        # `login_user(..., remember=True)` em `routes/auth.py` é o padrão deste
        # app. Sem `duracao_lembrete_horas` valeria o padrão do Flask-Login --
        # 365 dias -- num sistema com posição, custo e provento pessoais. Doze
        # horas alinha com o ConfortoTermico.
        duracao_horas=12,
        duracao_lembrete_horas=12,
    )

    if app.config["TRUST_PROXY_HEADERS"]:
        # Only trust X-Forwarded-* when actually deployed behind a reverse
        # proxy that sets them (Caddy/nginx terminating TLS).
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=1, x_proto=1, x_host=1
        )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf = iniciar_csrf(app)
    login_manager.init_app(app)
    limiter = iniciar_limiter(app)

    # SharedAuth aplica os cabecalhos defensivos e a CSP. A aplicação não usa
    # imagens `data:`, por isso não habilita `imagens_data_uri`.
    registrar_cabecalhos(app)

    # Componente comum de confirmação/aviso (modal + toast, CSS e JS puro) e
    # o banner de `flash()` com ícone por categoria. Os dois penduram um
    # Blueprint cada um; registrar aqui, junto do resto do sharedauth, é o
    # que deixa `sharedauth_ui.static` e `sharedauth.static`/`sharedauth/...`
    # resolvíveis nos templates.
    registrar_ui(app)
    registrar_mensagens(app)

    # O agente Windows entrega cotacoes por HTTPS. `RtdServiceManager` fornece
    # o estado exibido na tela; com o agente remoto habilitado, o estado nasce
    # indisponivel ate que o servidor receba o pulso do agente.
    from app.rtd_service import RtdServiceManager

    if app.config["REMOTE_COLLECTOR_ENABLED"]:
        app.extensions["rtd_service"] = RtdServiceManager(
            Path(app.root_path).parent,
            available=False,
            background_supervision=False,
        )
    else:
        # O subprocesso ``poll-rtd`` também cria a aplicação Flask para usar
        # as configurações e o banco. Ele não pode iniciar outro supervisor:
        # isso formaria uma cadeia infinita de coletores no host Windows.
        collector_process = os.getenv("RTD_COLLECTOR_PROCESS", "").lower() == "true"
        # `TESTING` também desliga a supervisão para que criar a aplicação em
        # testes no Windows não inicie threads nem sondagens do ProfitChart.
        supervisionar = not collector_process and not app.config["TESTING"]
        app.extensions["rtd_service"] = RtdServiceManager(
            Path(app.root_path).parent,
            background_supervision=supervisionar,
        )

    from app.cli import register_commands
    from app.presentation import register_filters
    from app.routes import register_blueprints

    register_blueprints(app)
    register_commands(app)
    register_filters(app)

    # `csrf`/`limiter` só existem depois de `iniciar_csrf`/`iniciar_limiter`
    # (uma instância por `create_app()`, não singleton de módulo — evita o
    # vazamento de isenção CSRF e o zeramento de contador de rate-limit entre
    # apps no mesmo processo). Por isso as rotas que precisavam decorar no
    # import de `auth.py`/`partials.py`/`collector_agent.py` são religadas
    # aqui, depois que já estão registradas. `RouteLimit.__call__` devolve uma
    # função *nova* — descartar o retorno em vez de reatribuir a
    # `view_functions` deixaria o limite decorado e nunca aplicado
    # (regressão real, já reproduzida e corrigida no MegaSena).
    aplicar_limite(app, limiter, "auth.login", LIMITE_LOGIN_PADRAO)
    aplicar_limite(
        app,
        limiter,
        ("portfolio.collector_heartbeat_partial", "portfolio.rtd_service_partial"),
        "120 per minute",
    )
    for endpoint in (
        "portfolio.collector_agent_quotes",
        "portfolio.collector_agent_failure",
    ):
        csrf.exempt(app.view_functions[endpoint])

    @app.context_processor
    def _collector_heartbeat_context() -> dict[str, object]:
        """Pulso do coletor, só onde alguma tela o mostra.

        Ele custa uma consulta por render. O indicador aparece na barra do
        menu em Ações e Cotações, e o controle do coletor vive em
        Configurações; nas demais páginas a consulta não teria leitor.
        """
        if request.endpoint not in HEARTBEAT_ENDPOINTS:
            return {}
        from app.collector_heartbeat import collector_heartbeat

        return {
            "collector_heartbeat": collector_heartbeat(
                stale_after_seconds=app.config["RTD_STALE_AFTER_SECONDS"]
            )
        }

    @app.context_processor
    def _theme_context() -> dict[str, str]:
        """Disponibiliza o tema persistido para a casca de todas as telas."""
        from app.themes import DEFAULT_THEME, THEME_IDS

        # A tela de login é pública e deve continuar renderizando mesmo nos
        # testes/ambientes em que o banco ainda não foi iniciado.
        if not current_user.is_authenticated:
            return {"app_theme": DEFAULT_THEME}
        from app.models import AppSetting

        settings = db.session.get(AppSetting, 1)
        theme = settings.theme if settings and settings.theme in THEME_IDS else DEFAULT_THEME
        return {"app_theme": theme}

    @app.context_processor
    def _privacy_context() -> dict[str, bool]:
        return {"values_hidden": bool(session.get("values_hidden", False))}

    requer_login(
        app,
        endpoints_publicos=PUBLIC_ENDPOINTS,
        endpoint_login="auth.login",
        esta_autenticado=lambda: current_user.is_authenticated,
        usar_hx_redirect=True,
    )

    return app
