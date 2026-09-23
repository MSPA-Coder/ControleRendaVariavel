"""Versão na URL dos estáticos, para o navegador guardá-los por um ano.

Sem isto, o Flask serve `app.css`, `app.js`, o htmx e os gráficos com
`Cache-Control: no-cache`: cada página aberta revalida cada arquivo, e são
oito idas ao servidor que só confirmam o que o navegador já tem. Não dava para
simplesmente ligar um `max-age` longo, porque aí um deploy que mudasse o CSS
seguiria servindo o antigo a quem já o tivesse em cache.

A saída é o arquivo carregar a própria versão na URL: `url_for('static', ...)`
acrescenta `?v=<hash do conteúdo>`, e a resposta de uma URL cuja versão bate
com o arquivo servido sai com um ano de cache e `immutable`. Mudou o conteúdo,
muda a URL, e o navegador busca o novo. Pedido sem `v` ou com `v` antigo segue
como antes -- revalidando --, para que uma página velha nunca fixe por um ano o
conteúdo novo sob um endereço antigo.

Vale para os estáticos do app e para os dos blueprints (`sharedauth`,
`sharedauth_ui`). O que um CSS referencia por `url()` relativo -- as fontes de
`fonts.css` -- não passa por `url_for` e continua revalidando.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from flask import Flask, request
from werkzeug.security import safe_join

PARAMETRO_VERSAO = "v"
UM_ANO_EM_SEGUNDOS = 365 * 24 * 60 * 60


def _eh_estatico(endpoint: str | None) -> bool:
    return endpoint is not None and (endpoint == "static" or endpoint.endswith(".static"))


def _pasta(app: Flask, endpoint: str) -> str | None:
    if endpoint == "static":
        return app.static_folder
    blueprint = app.blueprints.get(endpoint.rpartition(".")[0])
    return blueprint.static_folder if blueprint is not None else None


def instalar_versao_de_estaticos(app: Flask) -> None:
    # O hash é calculado uma vez por arquivo; a chave leva o instante e o
    # tamanho para que, fora da imagem (no `flask run` com recarga), uma edição
    # mude a versão sem reiniciar.
    versoes: dict[tuple[str, int, int], str] = {}

    def versao(endpoint: str, arquivo: str) -> str | None:
        pasta = _pasta(app, endpoint)
        caminho = safe_join(pasta, arquivo) if pasta else None
        if caminho is None:
            return None
        try:
            estado = Path(caminho).stat()
        except OSError:
            return None
        chave = (caminho, estado.st_mtime_ns, estado.st_size)
        if chave not in versoes:
            versoes[chave] = sha256(Path(caminho).read_bytes()).hexdigest()[:12]
        return versoes[chave]

    @app.url_defaults
    def _versionar(endpoint: str, valores: dict) -> None:
        if not _eh_estatico(endpoint) or PARAMETRO_VERSAO in valores:
            return
        arquivo = valores.get("filename")
        if arquivo:
            atual = versao(endpoint, arquivo)
            if atual is not None:
                valores[PARAMETRO_VERSAO] = atual

    @app.after_request
    def _cache_longo(resposta):
        if not _eh_estatico(request.endpoint) or resposta.status_code not in (200, 304):
            return resposta
        pedida = request.args.get(PARAMETRO_VERSAO)
        arquivo = (request.view_args or {}).get("filename")
        if not pedida or not arquivo or pedida != versao(request.endpoint, arquivo):
            return resposta
        resposta.cache_control.no_cache = None
        resposta.cache_control.public = True
        resposta.cache_control.max_age = UM_ANO_EM_SEGUNDOS
        resposta.cache_control.immutable = True
        return resposta
