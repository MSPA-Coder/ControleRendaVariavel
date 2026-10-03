"""O vocabulário comum que o CRV publica para quem consolida o patrimônio.

Identidade de titulares e instituições, e a forma de escrever dinheiro, quantidade
e preço no JSON. Quem decide o que foi pedido -- token, titular, data -- é a rota
`app.routes.patrimonio`; este módulo só formata.

O nome vem do tempo em que ele também montava a "fotografia" do patrimônio para o
NetWorth (contratos `patrimonio/v1` a `v3`, retirados em 03/10/2026). Hoje ele só
guarda o que o contrato v4 ainda usa.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

SISTEMA = "controle-renda-variavel"
CENTAVO = Decimal("0.01")


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


def dinheiro(valor: Decimal) -> str:
    """Dinheiro tem duas casas, e a multiplicação não sabe disso.

    `quantidade × preço` com duas colunas `Numeric(24,8)` produz uma escala de
    vinte e poucas casas -- `194796.0000000000000000000000`. O número está
    certo e a apresentação convida ao erro: quem consolidar pode arredondar
    diferente do que este sistema arredondaria, e os dois passam a discordar.
    """
    return format(valor.quantize(CENTAVO, rounding=ROUND_HALF_UP), "f")


def numero(valor: Decimal) -> str:
    """Quantidade e preço, sem zeros à direita e sem notação científica.

    `Decimal.normalize()` resolveria os zeros e criaria o outro problema:
    `Decimal("300").normalize()` vira `3E+2`, que nenhum consumidor espera.
    """
    texto = format(valor, "f")
    return texto.rstrip("0").rstrip(".") if "." in texto else texto
