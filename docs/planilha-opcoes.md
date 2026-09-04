# Contrato funcional: Opções

Este documento é a referência normativa das regras de opções implementadas
pelo sistema, nos mesmos termos de
[`planilha-acoes.md`](planilha-acoes.md). A origem das regras são as abas
`Opções` e `Rateio NF` da planilha `Trades.xlsm`, mantida apenas como
referência de leitura.

## Modelo

Contratos e posições são entidades distintas, para permitir vários lotes do
mesmo contrato. Cada contrato tem tipo `call` ou `put`, ticker próprio,
ativo-objeto, strike e vencimento.

| Conceito | Campo do sistema |
|---|---|
| corretora | `option_positions.broker_id` |
| ticker do contrato | `option_contracts.ticker_id` |
| compra/venda | `option_positions.side` |
| quantidade | `option_positions.quantity` |
| custo médio | `option_positions.average_cost` |
| target | `option_positions.target_price` |
| strike | `option_contracts.strike` |
| exercício | `option_expirations.exercise_date` |
| ativo-objeto | `option_contracts.underlying_ticker_id` |
| início | `option_positions.opened_on` |

## Cotações

- `ULT`, `FEC` e `EST` são lidos para o ticker da opção.
- `ULT` do ativo-objeto alimenta folga de strike e breakeven.
- Strike e exercício são dados cadastrais do contrato e do vencimento;
  indisponibilidade do RTD não os altera.
- O comando **Atualizar Cotações desde a abertura da posição** também importa
  o histórico diário do ticker de cada contrato de opção, desde a abertura
  mais antiga encontrada para esse ticker. Esse histórico alimenta a aba
  **Performance**; `OptionQuote` é somente o snapshot atual do RTD e não o
  substitui. Se um cadastro legado não tiver extrato de movimentos, a
  Performance pode usar uma abertura sintética somente para leitura, sem
  alterar os dados persistidos.

## Fórmulas

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

Divisões por zero produzem "não aplicável" (`None`).

## Gregas

As gregas usam Black-Scholes europeu, com a taxa livre de risco anual
configurada em Configurações (`app_settings.risk_free_rate_annual`). A
classificação de moneziness ("ITM", "ATM", "OTM") segue o tipo do contrato.

## Vencimentos

O calendário de vencimentos guarda código anual de call, código anual de put
e data de exercício. A interface permite criar, alterar e excluir vencimentos.
Um contrato cuja data de exercício já passou continua consultável no histórico,
mas não pode receber uma nova posição aberta.

## Invariantes

- Totais são derivados das posições e agrupados por vencimento.
- Posições reais não são versionadas: são cadastradas pela interface.
