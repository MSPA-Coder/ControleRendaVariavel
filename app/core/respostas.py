"""Respostas de erro em português e cabeçalho de cache das páginas.

Sem isto, um 404 ou um 500 saía como a página crua do Werkzeug: em inglês, sem
menu e sem caminho de volta, e um valor que o banco não aceita derrubava a
requisição com uma resposta 500 sem explicação. Aqui cada erro vira uma página
no mesmo casco do aplicativo (``error.html``) ou, quando a requisição veio do
HTMX, o aviso em toast que o fragmento ``request_error.html`` já transporta.
"""

from __future__ import annotations

from flask import Flask, Response, current_app, render_template, request
from flask.typing import ResponseReturnValue
from flask_wtf.csrf import CSRFError  # type: ignore[import-untyped]
from sqlalchemy.exc import DataError
from werkzeug.exceptions import HTTPException

from app import db

#: Título e mensagem de cada código. Quem chama `abort(400, description=...)`
#: com texto próprio (em português) tem a descrição preservada.
_TEXTOS: dict[int, tuple[str, str]] = {
    400: ("Requisição inválida", "Não foi possível entender o pedido. Volte e confira o que foi informado."),
    403: ("Acesso negado", "Sua conta não tem permissão para abrir esta tela."),
    404: (
        "Página não encontrada",
        "O endereço não existe ou o registro foi encerrado ou excluído. "
        "Volte à carteira e procure de novo.",
    ),
    405: ("Ação não permitida", "Esta tela não aceita esse tipo de pedido."),
    409: ("Conflito", "O registro mudou enquanto você o editava. Recarregue a tela e tente de novo."),
    413: ("Envio grande demais", "O arquivo ou o formulário enviado passa do tamanho aceito."),
    422: ("Valor não aceito", "Algum valor informado não pôde ser gravado. Volte e confira os números."),
    429: ("Muitas tentativas", "Aguarde um minuto antes de tentar de novo."),
    500: (
        "Algo deu errado",
        "O sistema não conseguiu concluir o pedido. Nada foi gravado pela metade; "
        "tente de novo e, se repetir, avise o administrador.",
    ),
}

_MENSAGEM_CSRF = (
    "A página ficou aberta por muito tempo ou a sessão mudou. "
    "Recarregue a tela e envie de novo."
)

#: Superfícies de máquina: o contrato delas é JSON ou texto, não uma página.
_PREFIXOS_DE_API = ("/api/", "/patrimonio/", "/fechamento/", "/health")


def _e_api() -> bool:
    return request.path.startswith(_PREFIXOS_DE_API)


def _descricao(codigo: int, erro: Exception) -> str:
    titulo, padrao = _TEXTOS.get(codigo, _TEXTOS[500])
    if isinstance(erro, CSRFError):
        return _MENSAGEM_CSRF
    descricao = getattr(erro, "description", None)
    # O Werkzeug devolve o texto em inglês quando ninguém passou descrição; só a
    # que o código do aplicativo escreveu (em português) vale ser mostrada.
    if isinstance(erro, HTTPException) and descricao and descricao != type(erro).description:
        return str(descricao)
    return padrao


def _renderizar(codigo: int, erro: Exception) -> ResponseReturnValue:
    try:
        return _pagina(codigo, erro)
    except Exception:
        # A página de erro também precisa do casco (usuário, tema, banco). Se o
        # que falhou foi justamente o banco, cair aqui devolve a resposta simples
        # com o código certo em vez de um segundo erro por cima do primeiro.
        current_app.logger.exception("falha ao montar a página de erro %s", codigo)
        titulo = _TEXTOS.get(codigo, _TEXTOS[500])[0]
        return f"{titulo}", codigo


def _pagina(codigo: int, erro: Exception) -> ResponseReturnValue:
    titulo = _TEXTOS.get(codigo, _TEXTOS[500])[0]
    mensagem = _descricao(codigo, erro)
    if request.headers.get("HX-Request") == "true":
        # O HTMX não troca respostas 4xx/5xx; o aviso entra no `main` sem
        # substituir a região que originou o pedido.
        return (
            render_template("partials/request_error.html", message=mensagem),
            codigo,
            {
                "HX-Retarget": "main",
                "HX-Reswap": "beforeend",
                "X-App-Request-Error": "1",
            },
        )
    return render_template("error.html", codigo=codigo, titulo=titulo, mensagem=mensagem), codigo


def registrar_erros(app: Flask) -> None:
    """Liga as páginas de erro e o ``Cache-Control`` das respostas HTML."""

    def _http(erro: HTTPException) -> ResponseReturnValue:
        codigo = erro.code or 500
        if _e_api() or codigo not in _TEXTOS:
            return erro
        return _renderizar(codigo, erro)

    for codigo in (400, 403, 404, 405, 409, 413, 429):
        app.register_error_handler(codigo, _http)

    @app.errorhandler(DataError)
    def _valor_fora_da_faixa(erro: DataError) -> ResponseReturnValue:
        # O banco recusou um valor (número grande demais, texto maior que a
        # coluna). A validação de entrada deve barrar isso antes; este é o
        # último recurso para que a pessoa receba uma explicação, e não um 500.
        db.session.rollback()
        current_app.logger.warning("valor recusado pelo banco: %s", erro.orig)
        return _renderizar(422, erro)

    @app.errorhandler(500)
    def _erro_interno(erro: Exception) -> ResponseReturnValue:
        db.session.rollback()
        if _e_api():
            return erro
        return _renderizar(500, erro)

    @app.after_request
    def _nao_guardar_paginas(resposta: Response) -> Response:
        """Páginas HTML trazem posição, custo e proventos: não ficam em cache.

        Sem isto, o botão "voltar" do navegador reabria, depois do logout, a
        última tela autenticada a partir do cache.
        """
        if resposta.mimetype == "text/html" and "Cache-Control" not in resposta.headers:
            resposta.headers["Cache-Control"] = "no-store"
            resposta.headers["Pragma"] = "no-cache"
        return resposta
