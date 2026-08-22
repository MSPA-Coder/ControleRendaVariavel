from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Flask, request
from flask_login import LoginManager, current_user  # type: ignore[import-untyped]
from flask_migrate import Migrate  # type: ignore[import-untyped]
from flask_sqlalchemy import SQLAlchemy
from sharedauth.access import requer_login
from sharedauth.csrf import iniciar_csrf
from sharedauth.messages import registrar_mensagens
from sharedauth.ratelimit import LIMITE_LOGIN_PADRAO, iniciar_limiter
from sharedauth.security import registrar_cabecalhos
from sharedauth.session import configurar_sessao
from sharedauth.ui import registrar_ui
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix

from app.secret_files import build_postgres_url, environment_value

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

    return db.session.get(User, int(user_id))


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    force_https = os.getenv("FORCE_HTTPS", "false").lower() == "true"
    configured_secret_key = environment_value("SECRET_KEY")
    configured_database_url = environment_value("DATABASE_URL")
    if not configured_database_url:
        postgres_password = environment_value("POSTGRES_PASSWORD")
        if postgres_password:
            configured_database_url = build_postgres_url(
                postgres_password,
                host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
                port=os.getenv("POSTGRES_PORT", "5302"),
                database=os.getenv("POSTGRES_DB", "investimentos"),
                username=os.getenv("POSTGRES_USER", "investimentos"),
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
        RTD_EXCEL_VISIBLE=os.getenv("RTD_EXCEL_VISIBLE", "false").lower() == "true",
        RTD_CONTROL_URL=os.getenv("RTD_CONTROL_URL", ""),
        RTD_CONTROL_TOKEN=environment_value("RTD_CONTROL_TOKEN") or "",
        COLLECTOR_AGENT_TOKEN=environment_value("COLLECTOR_AGENT_TOKEN") or "",
        REMOTE_COLLECTOR_ENABLED=os.getenv("REMOTE_COLLECTOR_ENABLED", "false").lower()
        == "true",
        FORCE_HTTPS=force_https,
        TRUST_PROXY_HEADERS=os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true",
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
        RATELIMIT_ENABLED=os.getenv("RATELIMIT_ENABLED", "true").lower() == "true",
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

    # Cabecalhos defensivos e CSP, do mesmo lugar que os outros apps.
    #
    # Isto substitui o Flask-Talisman, que saiu desta aplicacao. A politica
    # efetiva dele era identica, diretiva por diretiva, a que os outros
    # projetos escreviam a mao -- o que ele nao declarava caia em
    # `default-src 'self'` e dava no mesmo. O resto do que ele fazia ja tinha
    # dono: `deploy/nginx/controle-renda-variavel.conf` redireciona 80->443 e
    # emite o HSTS, e o cookie `Secure` vem de `configurar_sessao` acima.
    #
    # Some junto a inversao de ordem de registro que este arquivo dependia:
    # o handler proprio precisava ser registrado ANTES do Talisman para rodar
    # DEPOIS dele (o Flask executa os `after_request` na ordem inversa) e
    # conseguir sobrescrever o `Permissions-Policy` que o Talisman escrevia.
    # Era correto e funcionava, mas era conhecimento que morava num
    # comentario -- agora ha um registro so, sem ordem implicita.
    #
    # Sem `imagens_data_uri`: esta aplicacao nao tem favicon embutido nem
    # nenhuma imagem em `data:`.
    registrar_cabecalhos(app)

    # Componente comum de confirmação/aviso (modal + toast, CSS e JS puro) e
    # o banner de `flash()` com ícone por categoria. Os dois penduram um
    # Blueprint cada um; registrar aqui, junto do resto do sharedauth, é o
    # que deixa `sharedauth_ui.static` e `sharedauth.static`/`sharedauth/...`
    # resolvíveis nos templates.
    registrar_ui(app)
    registrar_mensagens(app)

    from app.rtd_service import RemoteRtdService, RtdServiceManager

    control_url = str(app.config["RTD_CONTROL_URL"])
    control_token = str(app.config["RTD_CONTROL_TOKEN"])
    if control_url and control_token:
        app.extensions["rtd_service"] = RemoteRtdService(control_url, control_token)
    else:
        # O subprocesso ``poll-rtd`` também cria a aplicação Flask para usar
        # as configurações e o banco. Ele não pode iniciar outro supervisor:
        # isso formaria uma cadeia infinita de coletores no host Windows.
        collector_process = os.getenv("RTD_COLLECTOR_PROCESS", "").lower() == "true"
        app.extensions["rtd_service"] = RtdServiceManager(
            Path(app.root_path).parent,
            background_supervision=not collector_process,
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
    app.view_functions["auth.login"] = limiter.limit(LIMITE_LOGIN_PADRAO)(
        app.view_functions["auth.login"]
    )
    for endpoint in (
        "portfolio.collector_heartbeat_partial",
        "portfolio.rtd_service_partial",
    ):
        app.view_functions[endpoint] = limiter.limit("120 per minute")(
            app.view_functions[endpoint]
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

    requer_login(
        app,
        endpoints_publicos=PUBLIC_ENDPOINTS,
        endpoint_login="auth.login",
        esta_autenticado=lambda: current_user.is_authenticated,
        usar_hx_redirect=True,
    )

    return app
