# Contrato funcional: Ações

Este documento é a referência normativa das regras de ações implementadas
pelo sistema. Ele descreve o comportamento vigente; quando o código e este
documento divergirem, a divergência é um defeito de um dos dois e deve ser
resolvida explicitamente (ver a ordem de precedência em `AGENTS.md`).

A origem das regras é a aba `Ações` da planilha `Trades.xlsm`, mantida
apenas como referência de leitura: ela não é banco de dados, dependência de
runtime nem destino de escrita.

## Modelo

Uma posição equivale a um lote. O mesmo ticker pode ter vários lotes, na
mesma corretora ou em corretoras diferentes.

| Conceito | Campo do sistema |
|---|---|
| corretora | `broker` |
| ticker | `ticker` |
| quantidade | `quantity` |
| custo médio | `average_cost` |
| tipo C/V | `side` |
| início | `opened_on` |
| delta da cotação | `quote_multiplier` |
| modo B (bruto) ou L (líquido) | `result_mode` |
| dias do ano | constante 365 |

Corretoras e tickers têm cadastros próprios. Mercado, código RTD e moeda
pertencem ao ticker; posições apenas referenciam esses cadastros.

## Cotações RTD

ProgID: `rtdtrading.rtdserver`.

- Tópico B3: `TICKER_B_0`.
- Tópicos EUA: `TICKER_Y_0` e `TICKER_N_0`.
- `ULT`: última cotação, usada como snapshot persistido.
- `FEC`: fechamento anterior.
- `EST`: status do instrumento; a interface mostra o primeiro caractere.

## Fórmulas

Para `q` quantidade, `c` custo, `p` preço atual, `f` fechamento, `d` dias,
`s = 1` para compra e `-1` para venda:

| Saída | Fórmula |
|---|---|
| Atual | `delta * preço RTD` |
| Var. dia | `s * (1 - f / p)` |
| Bruto | `s * q * (p - c)` |
| Líquido | `Bruto * 0,9996` |
| Retorno | `Resultado / (q * c)` |
| 365 | `sinal(r) * ((1 + abs(r)) ** (365 / d) - 1)` |
| Stop gain | `c * 1,5` |
| Distância do target | `Stop gain / p - 1` |
| Breakeven | `p/c - 1` quando `c < p`; caso contrário `-(c/p - 1)` |
| Desmontar | `s * q * p` |
| Montar | `-s * q * c` |
| Peso atual | `abs(Desmontar) / soma(abs(Desmontar))` |
| Peso de custo | `abs(Montar) / soma(abs(Montar))` |
| Dias | `hoje - início` |

Divisões por zero e anualização com zero dias produzem "não aplicável"
(`None`), nunca erro nem infinito.

## Valores de referência

Estes casos fixam o resultado esperado da função de resultado e servem de
oráculo para os testes unitários de domínio:

| Entrada | Resultado |
|---|---:|
| `Ge, 100 dias, C, 100, 10, 12, L` | `199,92` |
| `Ge, 100 dias, V, 100, 10, 12, L` | `-199,92` |
| `Ge, 100 dias, C, 100, 10, 12, B` | `200,00` |
| `Ge, 1550 dias, C, 1300, 14,20, 11,21, L` | `-3.885,4452` |

Corretora e prazo não alteram o resultado.

## Invariantes

- Totais são derivados das posições persistidas; não existem colunas
  redundantes no banco.
- Totais e pesos são separados por moeda; BRL e USD nunca são somados.
- Pesos usam como denominador a carteira visível dentro da mesma moeda,
  mantendo denominadores independentes por moeda.
- A grade pode ser filtrada entre posições reais e hipotéticas e por
  corretora; o filtro vale para os totais e pesos exibidos.
- Valores monetários e quantidades usam `Decimal`, nunca `float`.
- O coletor reutiliza uma única sessão Excel/RTD enquanto estiver em modo
  contínuo.
- Posições reais não são versionadas: são cadastradas pela interface, para
  manter dados pessoais fora do repositório.
