# Contrato da aba Opções

Fonte analisada em 27/07/2026:
`C:\Users\MSPA\Dropbox\Particulares\Bolsa\Trades\Trades.xlsm`, abas `Opções` e
`Rateio NF`. A planilha foi aberta somente para leitura.

O intervalo `Opções!T17:U24` foi desconsiderado conforme orientação do
mantenedor.

## Entradas por posição

| Coluna | Significado | Campo do sistema |
|---|---|---|
| `A` | corretora | `option_positions.broker_id` |
| `B` | ticker do contrato | `option_contracts.ticker_id` |
| `C` | compra/venda | `option_positions.side` |
| `D` | quantidade | `option_positions.quantity` |
| `E` | custo médio | `option_positions.average_cost` |
| `G` | target | `option_positions.target_price` |
| `T` | strike | `option_contracts.strike` |
| `AB` | exercício | `option_expirations.exercise_date` |
| `AE` | ativo-objeto | `option_contracts.underlying_ticker_id` |
| `AG` | início | `option_positions.opened_on` |

Cada contrato possui tipo `call` ou `put`, ticker próprio, ativo-objeto, strike
e vencimento. Contratos e posições são entidades distintas para permitir vários
lotes do mesmo contrato.

## Cotações

- `ULT`, `FEC` e `EST` são lidos para o ticker da opção.
- `ULT` do ativo-objeto alimenta folga de strike e breakeven.
- Strike e exercício são persistidos no cadastro do contrato e do vencimento;
  indisponibilidade RTD não altera esses dados cadastrais.

## Fórmulas traduzidas

Para direção `s = 1` em compra e `-1` em venda:

- variação diária: `s * (1 - fechamento / preço atual)`;
- variação total: `preço atual / custo - 1` em compra e
  `custo / preço atual - 1` em venda;
- resultado: mesma função líquida/bruta usada nas ações;
- retorno: `resultado / (quantidade * custo)`;
- desmontar: `s * quantidade * preço atual`;
- montar: `-s * quantidade * custo`;
- breakeven de call: `strike + prêmio`;
- breakeven de put: `strike - prêmio`;
- folga de strike de call: `ativo - strike`;
- folga de strike de put: `strike - ativo`;
- notional: `quantidade * strike` para posições vendidas;
- dias úteis: dias de segunda a sexta após a data atual até o exercício.

Divisões por zero produzem “não aplicável”.

## Vencimentos

`Rateio NF!M:O` contém o calendário com código anual de call, código anual de put
e data de exercício. Os 24 registros observados, de `2024F/2024R` a
`2026E/2026Q`, são carregados pela migração inicial da funcionalidade. A
interface permite criar, alterar e excluir vencimentos posteriores.

## Dados importados

Foram importadas seis posições reais preenchidas em `Opções!A4:AH9`, sem
registrá-las no código ou na migração. Totais são derivados das posições e
agrupados por vencimento.
