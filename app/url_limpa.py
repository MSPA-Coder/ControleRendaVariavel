"""Endereço canônico da tela: sem filtro no padrão e sem estado de interface.

Um formulário HTML serializa **todos** os seus campos quando é enviado,
inclusive os que estão no valor padrão e os que estão vazios. Sem tratamento, a
Carteira sem nenhum filtro aplicado aparece na barra de endereços como::

    /?portfolio_id=all&broker=&return_days=365

Nada ali foi escolhido por ninguém. É ruído que ocupa a barra, atrapalha quem
quer copiar o endereço e faz dois links iguais parecerem diferentes.

Este módulo monta o endereço equivalente sem esse ruído. A aplicação o entrega
no cabeçalho ``HX-Replace-Url`` das respostas de fragmento, e o navegador troca
a barra sem recarregar nada — ver ``_canonizar_url`` em ``app/__init__.py``.

**O filtro continua na URL quando é um filtro de verdade.** Só sai o que está
no padrão. `?broker=XP` aparece exatamente quando alguém escolheu XP, e o
endereço continua servindo para F5, favorito e link.
"""

from __future__ import annotations

from flask import request, url_for

from app.models import TransactionStatus

__all__ = [
    "ENDPOINTS_COM_BARRA",
    "ESTADO_DE_INTERFACE",
    "FILTROS_PADRAO",
    "url_canonica",
]

#: Telas inteiras, cujo endereço é o que aparece na barra. Fragmentos que só
#: existem para o HTMX (o pulso do coletor, o estado do RTD) ficam de fora: o
#: endereço deles nunca deve virar o endereço da página.
ENDPOINTS_COM_BARRA = frozenset(
    {
        "portfolio.index",
        "portfolio.transactions",
        "portfolio.dividends",
        "portfolio.monthly_performance",
        "portfolio.quote_history",
        "portfolio.exposure_asset",
        "portfolio.exposure_broker",
        "portfolio.exposure_market",
        "options.index",
    }
)

#: Valor que cada filtro assume quando ninguém escolheu nada. Precisa
#: acompanhar o padrão lido na rota correspondente — quando os dois divergem, o
#: parâmetro deixa de ser retirado e volta a aparecer na barra, que é uma falha
#: visível e inofensiva (ver a nota sobre parâmetro desconhecido, abaixo).
FILTROS_PADRAO: dict[str, str] = {
    # `routes/helpers.py::selected_filters`
    "portfolio_id": "all",
    # "Todas as corretoras" é a opção de valor vazio nos selects
    "broker": "",
    # `routes/positions.py::portfolio_results_context`
    "return_days": "365",
    # `routes/transactions.py`
    "status": TransactionStatus.CLOSED.value,
    # `routes/performance.py` e `monthly_performance.normalize_performance_period`
    "period": "all",
    "portfolio": "stocks",
    # comparadores opcionais: vazio quer dizer "nenhum"
    "benchmark_ticker_id": "",
    "ticker_id": "",
}

#: Parâmetros que descrevem como a tela está desenhada, não quais dados ela
#: mostra. Eles viajam na requisição do fragmento (o botão `+` de cada linha) e
#: **nunca** devem chegar à barra: são longos, não interessam a quem recebe o
#: link e mudam a cada clique.
ESTADO_DE_INTERFACE = frozenset({"expanded", "expanded_tickers", "expanded_years"})

#: Sentinela para "este parâmetro não tem padrão declarado". Não pode ser
#: ``None`` nem ``""``: os dois são valores possíveis de um filtro, e usar
#: qualquer um deles faria um filtro legítimo ser retirado da barra.
_SEM_PADRAO = object()


def url_canonica() -> str | None:
    """Endereço desta requisição sem ruído, ou ``None`` se não se aplica.

    Devolve ``None`` para endpoint que não é tela inteira — nesse caso a barra
    não deve ser tocada.

    **Parâmetro desconhecido é preservado, não descartado.** A escolha é
    deliberada: um filtro novo que alguém acrescente sem lembrar de
    :data:`FILTROS_PADRAO` continua funcionando na barra, e no máximo aparece
    com o valor padrão junto. O caminho oposto — descartar tudo que não está na
    tabela — faria esse mesmo filtro sumir do endereço em silêncio, quebrando
    favorito e link compartilhado sem nada apontar para a causa.

    Entre uma falha visível e uma silenciosa, esta função escolhe a visível.
    """
    if request.endpoint not in ENDPOINTS_COM_BARRA:
        return None

    mantidos = {
        chave: valor
        for chave, valor in request.args.items()
        if chave not in ESTADO_DE_INTERFACE
        and valor != FILTROS_PADRAO.get(chave, _SEM_PADRAO)
    }
    return url_for(request.endpoint, **mantidos)
