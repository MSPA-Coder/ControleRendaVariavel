from __future__ import annotations

import os
from typing import TYPE_CHECKING

from flask import Flask, request, session
from flask_login import LoginManager, current_user  # type: ignore[import-untyped]
from flask_migrate import Migrate  # type: ignore[import-untyped]
from flask_sqlalchemy import SQLAlchemy
from sharedauth.access import requer_login, requer_troca_de_senha
from sharedauth.config import ler_flag, montar_url_postgres
from sharedauth.csrf import iniciar_csrf
from sharedauth.messages import registrar_mensagens
from sharedauth.ratelimit import LIMITE_LOGIN_PADRAO, aplicar_limite, iniciar_limiter
from sharedauth.secrets import resolver_segredo
from sharedauth.security import registrar_cabecalhos
from sharedauth.session import (
    configurar_sessao,
    marca_de_sessao,
    marcas_conferem,
    separar_identificador,
)
from sharedauth.ui import registrar_ui
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix

from app.privacy import values_hidden

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

#: Onde o tema persistido fica guardado entre requisições. Ver
#: `_theme_context`: sem esse cache, descobrir o tema custava uma consulta em
#: todo render autenticado.
CHAVE_TEMA_NA_SESSAO = "app_theme"


def esquecer_tema_da_sessao() -> None:
    """Descarta o tema guardado, para a próxima página reler do banco.

    Chamada por quem grava o tema em Configurações. Sem isso, a pessoa
    trocaria o tema e continuaria vendo o antigo até a sessão terminar.
    """
    session.pop(CHAVE_TEMA_NA_SESSAO, None)


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
def _load_user(identificador: str) -> User | None:
    """Carrega o dono da sessão, conferindo a marca da senha.

    O identificador guardado no cookie é `id:marca` -- ver `User.get_id`. A
    marca não conferir significa que a senha mudou depois que aquele cookie foi
    emitido: a sessão cai, que é o efeito que faltava para trocar a senha
    derrubar quem entrou com a antiga.

    `int()` sem guarda derruba a requisição com 500 quando o identificador do
    cookie não é numérico. O cookie é assinado, então não é um caminho de
    ataque -- mas um cookie de formato antigo vira erro em vez de sessão
    recusada. O formato antigo (só o id) agora é recusado antes disso, por
    `separar_identificador`: as sessões abertas caem uma vez, no primeiro
    acesso depois do deploy. Mesma guarda do MegaSena.
    """
    from flask import current_app

    from app.models import User

    partes = separar_identificador(identificador)
    if partes is None:
        return None
    user_id, marca = partes
    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    if user is None or not user.is_active_user:
        return None
    atual = marca_de_sessao(user.password_hash, chave_secreta=current_app.secret_key)
    return user if marcas_conferem(marca, atual) else None


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    # `estrito=False` preserva o comportamento deste app: valor irreconhecível
    # cai no padrão em vez de impedir a subida. `FORCE_HTTPS` e
    # `TRUST_PROXY_HEADERS` são propriedades da implantação, e um typo aqui não
    # deve derrubar o serviço -- ele apenas não liga a folga.
    force_https = ler_flag("FORCE_HTTPS", estrito=False)
    configured_secret_key = resolver_segredo("SECRET_KEY")
    configured_database_url = resolver_segredo("DATABASE_URL")
    if not configured_database_url:
        postgres_password = resolver_segredo("POSTGRES_PASSWORD")
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
        COLLECTOR_AGENT_TOKEN=resolver_segredo("COLLECTOR_AGENT_TOKEN") or "",
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
    if app.config["TRUST_PROXY_HEADERS"] and not app.config["FORCE_HTTPS"]:
        # CRV-03: confiar em X-Forwarded-* só faz sentido atrás de um proxy
        # reverso que TERMINA TLS -- e se há TLS terminado à frente, o cookie
        # de sessão tem que sair com `Secure`. Até 02/09/2026 essa combinação
        # subia em silêncio: bastava `.env.vps` esquecer `FORCE_HTTPS=true`
        # (ou uma recriação do servidor sem o arquivo) para o cookie de sessão
        # ficar sem `Secure` sem nada avisar. Sem exceção para TESTING: não há
        # ambiente legítimo em que confiar no proxy e não exigir HTTPS façam
        # sentido juntos.
        raise RuntimeError(
            "TRUST_PROXY_HEADERS=true com FORCE_HTTPS=false: há um proxy reverso "
            "terminando TLS na frente (é para isso que TRUST_PROXY_HEADERS existe), "
            "e o cookie de sessão sairia sem Secure. Defina FORCE_HTTPS=true junto, "
            "ou desligue TRUST_PROXY_HEADERS se não há proxy de verdade."
        )

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

    # A aplicação web não inicia, supervisiona nem encerra coletor algum. Quem
    # é dono desse ciclo de vida é a tarefa do Windows (`scripts/rtd-agent.ps1`),
    # e o que a tela oferece é pausar e retomar a coleta -- um fato gravado em
    # `app_settings`, que o coletor lê no próximo intervalo de verificação.
    #
    # Isto foi um supervisor de processo aqui dentro. Com Gunicorn criando uma
    # fábrica por worker, aquilo significava um coletor candidato por worker
    # disputando a mesma sessão COM.

    from app.auditoria import registrar_escritas_financeiras
    from app.cli import register_commands
    from app.presentation import register_filters
    from app.routes import register_blueprints

    # Trilha de auditoria (S7). Autenticacao e gestao de contas registram
    # explicitamente, onde a acao tem nome; as escritas financeiras entram por
    # evento, para que uma rota nova nao nasca sem registro.
    registrar_escritas_financeiras()

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
    # CRV-02: as três rotas do agente são a ÚNICA superfície alcançável sem
    # sessão (estão em PUBLIC_ENDPOINTS), e duas delas são isentas de CSRF.
    # Sem limite, qualquer origem não autenticada podia martelar
    # `GET /api/collector/configuration` indefinidamente -- cada chamada
    # consome um worker do gunicorn (são 2) e uma consulta ao banco antes do
    # 401 sair. `override_defaults=True` porque `iniciar_limiter` não recebeu
    # `limites_padrao`: não há limite global para este substituir hoje, mas
    # a intenção fica explícita para quando houver.
    aplicar_limite(
        app,
        limiter,
        (
            "portfolio.collector_agent_configuration",
            "portfolio.collector_agent_quotes",
            "portfolio.collector_agent_failure",
        ),
        "60 per minute; 2000 per hour",
        override_defaults=True,
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
        """Disponibiliza o tema persistido para a casca de todas as telas.

        O valor fica na sessão depois da primeira leitura. Sem isso, esta
        função custava UMA CONSULTA POR RENDER AUTENTICADO -- em toda página,
        sempre -- para buscar um valor que quase nunca muda. É o mesmo
        cuidado que `_collector_heartbeat_context`, logo acima, já toma ao se
        restringir aos cinco endpoints que de fato mostram o pulso.

        Quem grava o tema em Configurações limpa a chave da sessão
        (`esquecer_tema_da_sessao`), então a troca aparece na próxima página
        sem esperar a sessão expirar.
        """
        from app.themes import DEFAULT_THEME, THEME_IDS

        # A tela de login é pública e deve continuar renderizando mesmo nos
        # testes/ambientes em que o banco ainda não foi iniciado.
        if not current_user.is_authenticated:
            return {"app_theme": DEFAULT_THEME}

        em_cache = session.get(CHAVE_TEMA_NA_SESSAO)
        if em_cache in THEME_IDS:
            return {"app_theme": em_cache}

        from app.models import AppSetting

        settings = db.session.get(AppSetting, 1)
        theme = settings.theme if settings and settings.theme in THEME_IDS else DEFAULT_THEME
        session[CHAVE_TEMA_NA_SESSAO] = theme
        return {"app_theme": theme}

    @app.context_processor
    def _privacy_context() -> dict[str, bool]:
        return {"values_hidden": values_hidden()}

    @app.after_request
    def _canonizar_url(resposta):
        """Limpa a barra de endereços das telas atualizadas por HTMX.

        Um formulário HTML serializa todos os seus campos, então a Carteira sem
        filtro nenhum chegava à barra como
        `/?portfolio_id=all&broker=&return_days=365` -- nada ali foi escolhido
        por ninguém. `HX-Replace-Url` entrega o endereço equivalente sem esse
        ruído, e o navegador troca a barra sem recarregar nada.

        `setdefault`: uma rota que já decidiu o próprio `HX-Replace-Url` (ou
        que use `HX-Redirect`) continua mandando.

        Só respostas 200 de GET: num 4xx/5xx a barra não deve passar a apontar
        para um endereço que não foi servido.
        """
        if request.method != "GET" or resposta.status_code != 200:
            return resposta
        if request.headers.get("HX-Request", "").lower() != "true":
            return resposta

        from app.url_limpa import url_canonica

        destino = url_canonica()
        if destino is not None:
            resposta.headers.setdefault("HX-Replace-Url", destino)
        return resposta

    requer_login(
        app,
        endpoints_publicos=PUBLIC_ENDPOINTS,
        endpoint_login="auth.login",
        esta_autenticado=lambda: current_user.is_authenticated,
        usar_hx_redirect=True,
    )

    # Senha redefinida por um administrador vale ate o primeiro acesso: com a
    # marca ligada, toda requisicao cai na tela de troca. Verificar so no login
    # deixaria a marca sem efeito -- bastaria digitar outra URL depois do
    # desvio para seguir usando a senha que o administrador conhece.
    #
    # `account.change_password` e isento pela propria biblioteca. Os daqui sao
    # os que faltam: sem `auth.logout` a pessoa fica presa dentro do
    # aplicativo, e sem os estaticos a tela de troca chega sem CSS. O
    # `portfolio.health` entra para o conteiner nao ser reportado como doente
    # justamente para quem esta com a senha vencida.
    requer_troca_de_senha(
        app,
        endpoint_troca="account.change_password",
        endpoints_isentos=frozenset(
            {
                "auth.logout",
                "portfolio.health",
                "static",
                "sharedauth.static",
                "sharedauth_ui.static",
            }
        ),
        esta_autenticado=lambda: current_user.is_authenticated,
        precisa_trocar=lambda: bool(current_user.must_change_password),
        usar_hx_redirect=True,
    )

    return app
