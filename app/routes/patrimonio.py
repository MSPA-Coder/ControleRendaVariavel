"""O resumo que este sistema publica para o consolidador de patrimônio.

É a metade gêmea da rota que o Controle Bancário já publica: mesmo caminho,
mesmo envelope, mesmo jeito de autenticar. Ele publica o **caixa**; este publica
o **investimento**. Quem soma é um terceiro aplicativo, só de leitura, que não
toca no banco de nenhum dos dois -- ler o banco alheio acoplaria os schemas e
quebraria a cada migration.

O VOCABULÁRIO COMUM

Titular e instituição são identificados pelo **nome normalizado**, não pelo id
do banco: "Genial" é a corretora 1 aqui e outra coisa lá, mas é a mesma
corretora para quem lê. `identidade()` tem de produzir exatamente o mesmo
resultado dos dois lados -- se um normalizar "Itaú" e o outro "itau", viram dois
titulares e o patrimônio aparece **dobrado**. Os dois repositórios têm o mesmo
teste, com os mesmos casos, justamente para isso não derivar em silêncio.

Todo valor viaja como **texto**: `float` não representa 0,10 e quem consolida
somaria centavos que nunca existiram.

TRÊS COISAS QUE ELE OMITE, E DIZ QUE OMITIU

1. **Carteira simulada.** Metade das posições desta base está numa, e somá-la ao
   patrimônio o infla com dinheiro que não existe -- sem que o número deixe de
   parecer plausível. A tela de Posições já trata "Todas" como as carteiras
   reais; aqui vale a mesma regra;
2. **Opções.** Elas têm valor e ficarão de fora até serem publicadas com o mesmo
   cuidado das ações. Enquanto isso, a contagem aparece no envelope;
3. **Posição sem cotação.** Sem preço não há valor a mercado, e inventar um
   seria pior do que faltar.

As três contagens vão em `omitidas`. Omissão contada é omissão visível; omissão
silenciosa é um patrimônio errado com cara de completo.
"""

from __future__ import annotations

import hmac
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from flask import abort, current_app, jsonify, request
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app import db
from app.models import Dividend, Position
from app.positions.portfolio import effective_position_quote
from app.routes import bp

CONTRATO = "patrimonio/v1"
SISTEMA = "controle-renda-variavel"

#: Janela dos proventos publicados. Eles não entram no patrimônio de hoje (já
#: foram recebidos e viraram caixa, que é do outro sistema); vão no envelope
#: porque o consolidador mostra renda do período. Sem janela, a lista cresceria
#: para sempre.
JANELA_DE_PROVENTOS_EM_DIAS = 365


def identidade(nome: str) -> str:
    """O identificador comum de um titular ou de uma instituição.

    Cópia deliberada de `core.patrimonio.identidade`, do Controle Bancário. Ela
    não mora no `sharedauth` ainda porque nasceu com um consumidor só; agora tem
    dois, e é candidata natural a subir para lá. Enquanto não sobe, o que impede
    as duas de derivarem é o teste com os mesmos casos nos dois repositórios.
    """
    decomposto = unicodedata.normalize("NFKD", nome or "")
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def _exigir_token() -> None:
    """Mesmo contrato do agente do coletor, e pelas mesmas razões.

    503 quando ninguém configurou a integração aqui; 401 quando o token está
    errado. A diferença importa: dizer 401 a quem nunca recebeu token mandaria o
    operador procurar por horas um segredo que nunca foi concedido.
    """
    configurado = str(current_app.config.get("PATRIMONIO_TOKEN") or "")
    if not configurado:
        abort(503, "Publicação de patrimônio não configurada.")
    apresentado = request.headers.get("Authorization", "")
    if not hmac.compare_digest(apresentado, f"Bearer {configurado}"):
        abort(401, "Não autorizado.")


def _titular() -> str:
    """De quem é o dinheiro que este sistema registra.

    Ele não sabe sozinho: `Position.owner_id` aponta para `users.id`, que é o
    usuário do aplicativo -- quem opera --, e não a pessoa dona do dinheiro. No
    Controle Bancário titular é outra entidade (`AccountOwner`: Mariano,
    Cláudia, Esther), e publicar o nome de usuário como se fosse titular
    funcionaria só enquanto os dois coincidissem.

    Por isso é configuração obrigatória, e não palpite: sem ela a rota não
    publica. No dia em que houver dinheiro de duas pessoas aqui, uma variável só
    deixa de bastar e isto vira um mapeamento por carteira.
    """
    nome = str(current_app.config.get("PATRIMONIO_TITULAR") or "").strip()
    if not nome:
        abort(
            503,
            "Publicação de patrimônio sem titular: defina PATRIMONIO_TITULAR com a "
            "pessoa dona do dinheiro registrado neste sistema.",
        )
    return nome


def _posicoes_reais() -> list[Position]:
    """Todas as posições de carteiras reais, de todos os donos.

    Sem escopo por usuário, e isso é deliberado -- igual ao outro publicador: um
    resumo filtrado produziria um patrimônio consolidado que esconde posições
    sem avisar, que é pior do que não responder.
    """
    consulta = (
        select(Position)
        .join(Position.portfolio_ref)
        .options(
            joinedload(Position.quote),
            joinedload(Position.broker_ref),
            joinedload(Position.ticker_ref),
            joinedload(Position.portfolio_ref),
        )
        .order_by(Position.id)
    )
    return list(db.session.scalars(consulta).unique().all())


def _proventos(desde: date) -> list[Dividend]:
    consulta = (
        select(Dividend)
        .where(Dividend.payment_date >= desde)
        .options(joinedload(Dividend.broker_ref), joinedload(Dividend.ticker_ref))
        .order_by(Dividend.payment_date, Dividend.id)
    )
    return list(db.session.scalars(consulta).unique().all())


CENTAVO = Decimal("0.01")


def _dinheiro(valor: Decimal) -> str:
    """Dinheiro tem duas casas, e a multiplicação não sabe disso.

    `quantidade × preço` com duas colunas `Numeric(24,8)` produz uma escala de
    vinte e poucas casas -- `194796.0000000000000000000000`. O número está
    certo e a apresentação convida ao erro: quem consolidar pode arredondar
    diferente do que este sistema arredondaria, e os dois passam a discordar.
    """
    return format(valor.quantize(CENTAVO, rounding=ROUND_HALF_UP), "f")


def _numero(valor: Decimal) -> str:
    """Quantidade e preço, sem zeros à direita e sem notação científica.

    `Decimal.normalize()` resolveria os zeros e criaria o outro problema:
    `Decimal("300").normalize()` vira `3E+2`, que nenhum consumidor espera.
    """
    texto = format(valor, "f")
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


@bp.get("/patrimonio/v1/resumo")
def patrimonio_resumo():
    _exigir_token()
    titular_nome = _titular()
    titular = identidade(titular_nome)

    pedida = (request.args.get("data") or "").strip()
    hoje = datetime.now(UTC).date()
    if pedida:
        try:
            referencia = date.fromisoformat(pedida)
        except ValueError:
            abort(400, "Data inválida: use AAAA-MM-DD.")
        if referencia != hoje:
            # Posição a mercado numa data passada exigiria reconstruir a posição
            # daquele dia E a cotação daquele dia. Aplicar a cotação de hoje a
            # uma carteira de março responderia um número que nunca existiu.
            # Publicar só o presente é uma limitação; publicar o passado errado
            # é um defeito.
            abort(
                400,
                "Este sistema só publica a posição de hoje: valor a mercado em data "
                "passada exigiria a cotação daquele dia, e aplicar a de hoje seria "
                "inventar o passado.",
            )
    referencia = hoje

    instituicoes: dict[str, dict] = {}
    linhas: list[dict] = []
    totais: dict[str, dict] = {}
    simuladas = 0
    sem_cotacao = 0
    agora = datetime.now(UTC)

    for posicao in _posicoes_reais():
        if posicao.simulated:
            simuladas += 1
            continue
        if posicao.quote is None:
            sem_cotacao += 1
            continue
        preco, observado_em = effective_position_quote(posicao)
        direcao = Decimal("1") if posicao.side.value == "C" else Decimal("-1")
        valor = direcao * posicao.quantity * preco * posicao.quote_multiplier
        instituicao = identidade(posicao.broker)
        instituicoes[instituicao] = {
            "id": instituicao,
            "nome": posicao.broker,
            "tipo": "Corretora",
        }
        linhas.append(
            {
                "id": f"{SISTEMA}:posicao:{posicao.id}",
                "titular": titular,
                "instituicao": instituicao,
                "instrumento": posicao.ticker,
                "classe": "acao",
                "mercado": posicao.ticker_ref.market.value,
                "quantidade": _numero(posicao.quantity * direcao),
                "moeda": posicao.currency,
                "preco": _numero(preco * posicao.quote_multiplier),
                "valor_a_mercado": _dinheiro(valor),
                "preco_em": observado_em.isoformat(),
                "fonte_do_preco": SISTEMA,
                # O estado que o próprio coletor gravou. A idade do preço quem
                # diz é `preco_em`, e é ela que o consumidor deve usar para
                # julgar: o limiar de "velho" desta casa é de 30 segundos,
                # pensado para uma tela ao vivo durante o pregão, e aplicá-lo a
                # uma foto diária marcaria como velho todo preço fora do horário
                # de mercado -- um alarme que toca sempre não avisa nada.
                "situacao_do_preco": posicao.quote.source_status,
            }
        )
        bloco = totais.setdefault(
            posicao.currency, {"moeda": posicao.currency, "total": Decimal("0"), "linhas": 0}
        )
        bloco["total"] += valor
        bloco["linhas"] += 1

    desde = referencia - timedelta(days=JANELA_DE_PROVENTOS_EM_DIAS)
    proventos = []
    for provento in _proventos(desde):
        instituicao = identidade(provento.broker)
        instituicoes.setdefault(
            instituicao, {"id": instituicao, "nome": provento.broker, "tipo": "Corretora"}
        )
        proventos.append(
            {
                "id": f"{SISTEMA}:provento:{provento.id}",
                "titular": titular,
                "instituicao": instituicao,
                "instrumento": provento.ticker_ref.symbol,
                "tipo": provento.kind.value,
                "moeda": provento.ticker_ref.currency,
                "valor": _dinheiro(provento.amount),
                "data": provento.payment_date.isoformat(),
            }
        )

    resposta = jsonify(
        {
            "contrato": CONTRATO,
            "sistema": SISTEMA,
            "papel": "investimento",
            "gerado_em": agora.isoformat(),
            "data_de_referencia": referencia.isoformat(),
            "titulares": [{"id": titular, "nome": titular_nome}],
            "instituicoes": [instituicoes[chave] for chave in sorted(instituicoes)],
            # Conta é do outro publicador: o caixa das corretoras vive no
            # Controle Bancário desde a decisão de 16/09/2026.
            "contas": [],
            "totais_por_moeda": [
                {"moeda": moeda, "total": _dinheiro(dados["total"]), "linhas": dados["linhas"]}
                for moeda, dados in sorted(totais.items())
            ],
            "posicoes": linhas,
            "proventos": proventos,
            "proventos_desde": desde.isoformat(),
            "ativos_alternativos": [],
            "omitidas": {
                "simuladas": simuladas,
                "opcoes": _quantas_opcoes(),
                "sem_cotacao": sem_cotacao,
            },
        }
    )
    # A foto carrega a carteira inteira: nenhum intermediário deve guardá-la.
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def _quantas_opcoes() -> int:
    from app.models import OptionPosition

    return int(
        db.session.scalar(
            select(db.func.count())
            .select_from(OptionPosition)
            .join(OptionPosition.portfolio_ref)
        )
        or 0
    )
