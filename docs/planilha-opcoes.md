# Contrato funcional: Opções

Se a posição mudar depois que o formulário de edição for aberto, o envio é
recusado e a pessoa precisa recarregar os dados antes de editar novamente.

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

Ao corrigir a data inicial da posição, o sistema atualiza a abertura do extrato
e a data inicial das transações parciais vinculadas. A nova data não pode
colocar a abertura depois de outro movimento; se um extrato legado não tiver
abertura, a alteração é recusada para revisão.
Quando há encerramentos parciais, a edição não permite trocar contrato,
corretora, carteira ou lado; os lançamentos históricos devem ser corrigidos
separadamente.

Ao excluir a transação de um encerramento parcial de posição ainda aberta,
o encerramento é desfeito e os movimentos seguintes são reaplicados. Custos
médios e resultados das transações de encerramento posteriores são recalculados
conforme a direção comprada ou vendida da posição.
Encerramentos parciais retroativos também reaplicam o extrato; são recusados se
deixarem saldo histórico zerado ou negativo. Encerramentos totais anteriores a
outros movimentos são recusados. Aportes anteriores a movimentos registrados
também reaplicam o extrato e os resultados posteriores.
Um aporte anterior à abertura de uma posição legada sem extrato é recusado
para revisão, pois não há histórico suficiente para reconstruí-lo com segurança.

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

- variação diária: `s * (preço atual / fechamento - 1)`, contra o fechamento,
  como nas ações — a planilha usa `s * (1 - fechamento / preço atual)`, e a
  divergência está explicada em `planilha-acoes.md`;
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

### Limitação de unidade e tamanho do contrato

O aplicativo calcula os valores como `quantidade × prêmio` e
`quantidade × strike`, sem um campo de tamanho ou fator do contrato. A
quantidade cadastrada precisa, portanto, estar na unidade compatível com a
cotação usada. A especificação da B3 informa que o tamanho do contrato
corresponde à quantidade de ativos-objeto e que o prêmio pode ser cotado por
unidade ou por lote, conforme o fator de cotação ([especificação da B3](https://b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-variavel/opcoes-sobre-acoes.htm)).
O aplicativo não consulta essas especificações nem consegue deduzir o
significado da quantidade pelos valores lançados; confira cada série na B3.
Esta aproximação atende ao uso ocasional atual, mas não substitui cálculo
oficial de opções.

## Gregas

As gregas usam Black-Scholes europeu, com a taxa livre de risco anual
configurada em Configurações (`app_settings.risk_free_rate_annual`). A
classificação de moneziness ("ITM", "ATM", "OTM") segue o tipo do contrato.

A inversão do prêmio para volatilidade implícita usa o limite europeu com
strike descontado: `max(S − K exp(−rT), 0)` para call e
`max(K exp(−rT) − S, 0)` para put, sem dividendos. Um prêmio europeu válido
pode ficar abaixo do valor de exercício imediato. A suíte confere um preço
publicado pelo QuantLib, paridade call/put e gregas por diferenças finitas.

## Vencimentos

O calendário de vencimentos guarda código anual de call, código anual de put
e data de exercício. A interface permite criar, alterar e excluir vencimentos.
Um contrato cuja data de exercício já passou continua consultável no histórico,
mas não pode receber uma nova posição aberta.

## Invariantes

- Totais são derivados das posições e agrupados por vencimento.
- Posições reais não são versionadas: são cadastradas pela interface.
