# Contrato da aba Ações

Fonte analisada em 27/07/2026:
`C:\Users\MSPA\Dropbox\Particulares\Bolsa\Trades\Trades.xlsm`, aba `Ações`.
A planilha foi aberta somente para leitura.

## Estrutura observada

A área usada é `A1:Y31`. Os dados ficam em três blocos principais:

- B3, linhas 4 a 11;
- EUA, linhas 13 a 25;
- lotes adicionais, linhas 29 a 31.

As linhas vazias são apenas separadores visuais. O sistema não depende delas.
Uma posição persistida equivale a uma linha/lote; tickers repetidos são válidos.

## Entradas

| Célula/coluna | Significado | Campo do sistema |
|---|---|---|
| `C1` | delta da cotação | `quote_multiplier` |
| `I1` | modo B ou L | `result_mode` |
| `I2` | dias do ano | constante 365 |
| `A` | corretora | `broker` |
| `B` | ticker | `ticker` |
| `C` | quantidade | `quantity` |
| `D` | custo médio | `average_cost` |
| `S` | tipo C/V | `side` |
| `U` | início | `opened_on` |

## RTD

ProgID observado: `rtdtrading.rtdserver`.

- Tópico B3: `TICKER_B_0`.
- Tópicos EUA observados: `TICKER_Y_0` e `TICKER_N_0`.
- `ULT`: última cotação.
- `FEC`: fechamento anterior.
- `EST`: status do instrumento; a planilha mostra o primeiro caractere.
- Para estados contendo `A.L`, a fórmula original escolhe `OCP` em compras e
  `OVD` em vendas. O primeiro release usa `ULT` como snapshot persistido; essa
  ramificação deve ser adicionada ao adapter se houver posições nesses estados.

## Fórmulas traduzidas

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

Divisões por zero e anualização com zero dias produzem “não aplicável”.

## Validação da função interna

`ResultadoOperacao` foi executada no Excel com entradas sintéticas:

| Entrada | Resultado |
|---|---:|
| `Ge, 100 dias, C, 100, 10, 12, L` | `199,92` |
| `Ge, 100 dias, V, 100, 10, 12, L` | `-199,92` |
| `Ge, 100 dias, C, 100, 10, 12, B` | `200,00` |
| `Ge, 1550 dias, C, 1300, 14,20, 11,21, L` | `-3.885,4452` |

Corretora e prazo não alteraram o resultado nos casos exercitados.

## Decisões de implementação

- Totais são derivados; não há colunas redundantes no banco.
- Corretoras e tickers possuem cadastros próprios. Mercado, código RTD e moeda
  pertencem ao ticker; posições apenas referenciam esses cadastros.
- Totais e pesos são separados por moeda; BRL e USD nunca são somados.
- A grade pode ser filtrada entre posições reais e hipotéticas e por corretora.
- As posições importadas da corretora `Av` são classificadas como hipotéticas.
- O coletor reutiliza uma única sessão Excel/RTD enquanto estiver em modo
  contínuo.
- Pesos são calculados sobre a carteira visível dentro da mesma moeda, mantendo
  denominadores independentes para BRL e USD.
- As posições reais da planilha não foram copiadas para código ou migração.
  Devem ser cadastradas pela interface para manter dados pessoais fora do
  repositório.
