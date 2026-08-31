"""Quem fez o quê, e quando.

Este é um sistema multiusuário sobre dado financeiro pessoal: posição, custo,
provento. Até agosto/2026 nada registrava quem entrou, quem encerrou uma
posição ou quem desativou uma conta — e a pergunta "quem mudou isto?" não tinha
resposta em lugar nenhum.

**O que se registra, e o que não.** Só o que muda estado ou acesso: entrada e
saída, gestão de contas, e as escritas que alteram a carteira. Consulta não
entra — uma trilha que registra leitura cresce depressa, dilui o que importa e
não responde nenhuma pergunta que se faça de verdade aqui.

**A trilha não pode derrubar a operação.** Um erro ao registrar é engolido e
vai para o log da aplicação: perder o registro de um encerramento é ruim;
impedir o encerramento por causa disso é pior. Ver :func:`registrar`.

A persistência mora aqui, no aplicativo, e não no SharedAuth — a carta da
biblioteca proíbe persistência. O que vem de lá é só
:func:`sharedauth.logs.sanitizar_log`, que trata o texto de terceiro.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import has_request_context, request
from flask_login import current_user  # type: ignore[import-untyped]
from sharedauth.logs import sanitizar_log

from app import db
from app.models import AuditLog

__all__ = ["ACOES", "ENTIDADES_FINANCEIRAS", "registrar", "registrar_escritas_financeiras"]

_log = logging.getLogger(__name__)

#: Vocabulário fechado das ações registradas. Fechado de propósito: uma trilha
#: em que cada ponto inventa o próprio verbo não se consulta por ação, que é a
#: consulta mais útil que ela tem a oferecer.
ACOES: frozenset[str] = frozenset(
    {
        "login",
        "login_recusado",
        "logout",
        "criar",
        "atualizar",
        "excluir",
        "encerrar",
        "ativar",
        "desativar",
        "redefinir_senha",
        # A troca feita pelo proprio dono. Verbo distinto de
        # "redefinir_senha", que e a acao de um administrador sobre a conta de
        # outra pessoa: quem consulta a trilha precisa separar as duas.
        "trocar_senha",
        "importar",
    }
)


def registrar(
    entidade: str,
    acao: str,
    *,
    entidade_id: object = None,
    detalhes: dict[str, Any] | None = None,
    usuario_id: int | None = None,
) -> None:
    """Grava uma linha na trilha, sem nunca interromper quem a chamou.

    ``usuario_id`` explícito serve ao login recusado, em que não há sessão — e
    ao caso simétrico do login aceito, registrado antes de a sessão existir.
    Fora isso o autor sai de ``current_user``.

    **Não faz commit.** A gravação participa da transação do caso de uso que a
    chamou: se aquele encerramento falhar e voltar atrás, a trilha não pode
    ficar afirmando que ele aconteceu.
    """
    if acao not in ACOES:
        # Vocabulário desconhecido é defeito de programação, não de dado. Não
        # derruba a operação, mas não passa em silêncio.
        _log.warning("acao fora do vocabulario da trilha: %s", sanitizar_log(acao))

    try:
        db.session.add(
            AuditLog(
                user_id=usuario_id if usuario_id is not None else _usuario_da_sessao(),
                entity=entidade[:80],
                entity_id=str(entidade_id)[:80] if entidade_id is not None else None,
                action=acao[:40],
                details=_limpar(detalhes),
                ip=_endereco(),
            )
        )
    except Exception:  # noqa: BLE001 - ver o docstring: a trilha nao derruba nada
        _log.exception("falha ao registrar na trilha de auditoria")


def _usuario_da_sessao() -> int | None:
    try:
        return int(current_user.id) if current_user.is_authenticated else None
    except (AttributeError, TypeError, ValueError):
        return None


def _endereco() -> str | None:
    """Endereço de origem, quando há requisição.

    Atrás do Nginx o `remote_addr` só é o endereço real porque `ProxyFix` está
    ligado quando `TRUST_PROXY_HEADERS` está ativo; sem ele seria sempre o do
    proxy. Ver `create_app`.
    """
    if not has_request_context():
        return None
    return (request.remote_addr or "")[:45] or None


def _limpar(detalhes: dict[str, Any] | None) -> dict[str, Any] | None:
    """Passa todo texto por `sanitizar_log` antes de ele virar linha gravada.

    Vale para valor **e** para chave: um dicionário montado a partir de um
    formulário pode ter as duas coisas vindas de fora.
    """
    if not detalhes:
        return None
    return {
        sanitizar_log(chave): (sanitizar_log(valor) if isinstance(valor, str) else valor)
        for chave, valor in detalhes.items()
    }


# ---------------------------------------------------------------------------
# Escritas financeiras
# ---------------------------------------------------------------------------

#: Entidades cuja escrita entra na trilha sozinha, sem chamada em rota nenhuma.
#:
#: São as que respondem "quem mudou minha carteira?". Ficam de fora os
#: cadastros (corretora, ticker) e tudo que a coleta escreve sozinha — cotação
#: e histórico de preço chegam do agente a cada poucos segundos e afogariam a
#: trilha com movimento que nenhuma pessoa fez.
ENTIDADES_FINANCEIRAS: frozenset[str] = frozenset(
    {
        "Position",
        "OptionPosition",
        "Transaction",
        "Dividend",
        "PositionMovement",
        "OptionPositionMovement",
    }
)


#: `create_app()` roda uma vez por processo em produção e muitas vezes na
#: suíte. O ouvinte é registrado na classe `Session`, que é global: sem esta
#: guarda, a segunda aplicação criada no mesmo processo duplicaria cada linha
#: da trilha -- e o sintoma apareceria só em teste, ou pior, só depois.
_ouvinte_ligado = False


def registrar_escritas_financeiras(_db: Any = None) -> None:
    """Liga o registro automático das escritas de :data:`ENTIDADES_FINANCEIRAS`.

    Por evento, e não por chamada em cada rota, por dois motivos. O primeiro é
    que são dezoito pontos hoje, e uma rota nova nasceria sem o registro — o
    tipo de esquecimento que só se descobre quando a trilha é consultada e não
    tem a resposta. O segundo é que a pergunta que a trilha responde é sobre o
    DADO ("quem mudou esta posição?"), não sobre a rota; ancorá-la na escrita é
    ancorá-la onde a pergunta mora.

    O preço é que a ação vem do ORM (`criar`/`atualizar`/`excluir`) e não do
    vocabulário do caso de uso: um encerramento aparece como as escritas que o
    compõem. Para a pergunta que se faz aqui, isso basta; quando não bastar, o
    caso de uso pode chamar :func:`registrar` explicitamente e os dois convivem.
    """
    global _ouvinte_ligado
    if _ouvinte_ligado:
        return

    from sqlalchemy import event, insert
    from sqlalchemy.orm import Session

    @event.listens_for(Session, "before_flush")
    def _coletar(sessao, _contexto, _instancias):  # type: ignore[no-untyped-def]
        """Anota o que mudou, enquanto ainda dá para saber.

        Só aqui `sessao.new`, `.dirty` e `.deleted` estão preenchidos; depois do
        flush eles já foram esvaziados.
        """
        pendentes = sessao.info.setdefault("trilha_pendente", [])
        for entidades, acao in (
            (sessao.new, "criar"),
            (sessao.dirty, "atualizar"),
            (sessao.deleted, "excluir"),
        ):
            for entidade in list(entidades):
                if type(entidade).__name__ not in ENTIDADES_FINANCEIRAS:
                    continue
                if acao == "atualizar" and not sessao.is_modified(entidade):
                    # `dirty` é otimista: lista o que FOI TOCADO, não o que
                    # mudou de valor. Sem esta guarda, abrir uma tela de edição
                    # e salvar sem alterar nada deixaria rastro de alteração.
                    continue
                pendentes.append((entidade, acao, _resumo(entidade)))

    @event.listens_for(Session, "after_flush")
    def _gravar(sessao, _contexto):  # type: ignore[no-untyped-def]
        """Grava depois do flush, que é quando a linha nova finalmente tem id.

        Vai por `insert()` do Core, não por `session.add`: um objeto acrescentado
        aqui não entraria neste flush, e a linha de criação sairia sem o id --
        justamente o campo pela qual a trilha é consultada ("quem mexeu na
        posição 42?"). O Core escreve na mesma transação, sem passar de novo
        pela unidade de trabalho e sem risco de recursão.
        """
        pendentes = sessao.info.pop("trilha_pendente", None)
        if not pendentes:
            return
        try:
            sessao.execute(
                insert(AuditLog),
                [
                    {
                        "user_id": _usuario_da_sessao(),
                        "entity": type(entidade).__name__[:80],
                        "entity_id": str(entidade.id)[:80] if entidade.id is not None else None,
                        "action": acao,
                        "details": resumo,
                        "ip": _endereco(),
                    }
                    for entidade, acao, resumo in pendentes
                ],
            )
        except Exception:  # noqa: BLE001 - ver o docstring do modulo
            _log.exception("falha ao registrar escrita financeira na trilha")

    _ouvinte_ligado = True


def _resumo(entidade: object) -> dict[str, Any] | None:
    """Campos que identificam a linha para quem for ler a trilha depois.

    Não é uma cópia do registro: guardar o antes e o depois de tudo faria a
    trilha crescer mais que os dados que ela audita. O que fica é o suficiente
    para reconhecer a linha e ir olhá-la.
    """
    resumo = {
        campo: valor
        for campo in ("ticker_id", "portfolio_id", "broker_id", "side", "quantity", "status")
        if (valor := getattr(entidade, campo, None)) is not None
    }
    return {chave: str(valor) for chave, valor in resumo.items()} or None
