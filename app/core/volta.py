"""Volta de uma escrita para a tela de onde ela partiu, com os filtros que ela mostrava.

Os filtros globais (moeda, carteira, corretora) vivem na URL, de propósito:
duas abas podem filtrar de modos diferentes sem disputar um estado salvo. O
preço disso é que toda escrita (salvar, excluir, encerrar...) é um POST cuja
resposta é um redirect, e o redirect montado no servidor não sabia de onde a
pessoa veio: voltava para a lista **sem query**. Moeda ausente vale BRL, então
quem olhava as posições em dólar via só as de reais (auditoria de 09/10/2026,
padrões CP1 e CP2).

Em vez de reescrever cada um dos ~60 pontos de redirect, a volta é costurada
num único lugar: depois da view, se a resposta de um POST é um redirect para
uma tela do próprio sistema, os filtros da origem entram no ``Location``.

A origem sai daqui, nesta ordem:

1. o campo ``volta`` do formulário, que ``app.js`` preenche no envio com o
   endereço **atual** da página (caminho e query);
2. o cabeçalho ``HX-Current-URL`` que o HTMX manda em toda requisição;
3. o ``Referer`` do navegador.

Um formulário de página (nova posição, editar, encerrar...) é aberto por um
link que leva ``?volta=<endereço da lista>``; quando o envio parte dessa
página, a origem de verdade é a lista que está dentro de ``volta``.

Só valores (a query) são aproveitados; o destino é sempre o que a rota
escolheu, então nada aqui permite redirecionar para outro lugar. O que a rota
já pôs no ``Location`` nunca é sobrescrito.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit

from flask import Flask, Response, current_app, request
from werkzeug.exceptions import NotFound

from app.core.currency_filter import CURRENCY_FILTER_VALUES, DEFAULT_CURRENCY_FILTER
from app.core.url_limpa import ENDPOINTS_COM_BARRA, ESTADO_DE_INTERFACE

__all__ = ["PARAMETROS_GLOBAIS", "endereco_de_volta", "instalar_volta_com_filtros"]

#: Filtros que acompanham a pessoa em qualquer tela que os entenda.
PARAMETROS_GLOBAIS = ("currency", "portfolio_id", "broker")

#: Destinos em que só a moeda faz sentido: ``portfolio_id`` nas tabelas
#: cadastrais é a carteira selecionada para edição, não um filtro.
_SO_MOEDA_PREFIXOS = ("/tables/",)
#: Telas em que a volta nunca interfere (login, troca de senha...).
_ENDPOINTS_IGNORADOS_PREFIXOS = ("auth.", "account.")
_TAMANHO_MAXIMO = 2000
_REDIRECTS = frozenset({301, 302, 303, 307, 308})


def endereco_de_volta(bruto: str | None) -> tuple[str, list[tuple[str, str]]] | None:
    """``(caminho, query)`` de um endereço relativo ao próprio sistema.

    Recusa o que não é caminho local: outro host, esquema, ``//``, barra
    invertida ou tamanho absurdo.
    """
    if not bruto or len(bruto) > _TAMANHO_MAXIMO:
        return None
    partes = urlsplit(bruto)
    if partes.scheme or partes.netloc or not partes.path.startswith("/") or partes.path.startswith("//"):
        return None
    if "\\" in bruto:
        return None
    return partes.path, parse_qsl(partes.query, keep_blank_values=True)


def _origem() -> tuple[str, list[tuple[str, str]]] | None:
    bruto = request.form.get("volta") or None
    if bruto is None:
        for cabecalho in ("HX-Current-URL", "Referer"):
            atual = request.headers.get(cabecalho)
            if not atual:
                continue
            partes = urlsplit(atual)
            if partes.netloc and partes.netloc != request.host:
                continue
            bruto = partes.path + (f"?{partes.query}" if partes.query else "")
            break
    return endereco_de_volta(bruto)


def _endpoint_de(caminho: str) -> str | None:
    try:
        endpoint, _ = current_app.url_map.bind("localhost").match(caminho, method="GET")
    except NotFound:
        return None
    except Exception:  # noqa: BLE001 - caminho estranho: a volta simplesmente não age
        return None
    return endpoint


def _valor_aceito(chave: str, valor: str) -> bool:
    if valor == "":
        return False
    if chave == "currency":
        return valor.upper() in CURRENCY_FILTER_VALUES and valor.upper() != DEFAULT_CURRENCY_FILTER
    return len(valor) <= 200


def _query_a_levar(
    destino: str, endpoint: str, origem: tuple[str, list[tuple[str, str]]]
) -> list[tuple[str, str]]:
    caminho, query = origem
    mesma_tela = caminho == destino
    # Veio de um formulário de página: a lista de verdade está em ``volta``.
    aninhada = next((valor for chave, valor in query if chave == "volta"), None)
    if aninhada is not None and not mesma_tela:
        interna = endereco_de_volta(aninhada)
        if interna is not None:
            caminho, query = interna
            mesma_tela = caminho == destino
    if mesma_tela:
        return [
            (c, v)
            for c, v in query
            if c not in ESTADO_DE_INTERFACE and (c == "volta" or _valor_aceito(c, v))
        ]
    if destino.startswith(_SO_MOEDA_PREFIXOS):
        permitidos: tuple[str, ...] = ("currency",)
    elif endpoint in ENDPOINTS_COM_BARRA:
        permitidos = PARAMETROS_GLOBAIS
    else:
        permitidos = ("currency",)
    return [(c, v) for c, v in query if c in permitidos and _valor_aceito(c, v)]


def voltar_ou(padrao: str) -> str:
    """Destino do "Cancelar": a lista de onde o formulário foi aberto, ou ``padrao``."""
    bruto = request.args.get("volta")
    return bruto if endereco_de_volta(bruto) is not None else padrao


def instalar_volta_com_filtros(app: Flask) -> None:
    """Registra o ``after_request`` que costura a volta dos POSTs."""
    app.jinja_env.globals["voltar_ou"] = voltar_ou

    @app.after_request
    def _levar_filtros_na_volta(resposta: Response) -> Response:
        if request.method != "POST" or resposta.status_code not in _REDIRECTS:
            return resposta
        local = resposta.headers.get("Location")
        destino = endereco_de_volta(local) if local and "#" not in local else None
        origem = _origem()
        if destino is None or origem is None:
            return resposta
        caminho, ja_na_url = destino
        endpoint = _endpoint_de(caminho)
        if endpoint is None or endpoint.startswith(_ENDPOINTS_IGNORADOS_PREFIXOS):
            return resposta
        presentes = {chave for chave, _ in ja_na_url}
        extras = [
            (chave, valor)
            for chave, valor in _query_a_levar(caminho, endpoint, origem)
            if chave not in presentes
        ]
        if extras:
            resposta.headers["Location"] = f"{caminho}?{urlencode(ja_na_url + extras)}"
        return resposta
