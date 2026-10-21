# Conceitos financeiros da frota

Texto-base mantido em `manutencao/docs/CONCEITOS_FINANCEIROS.md`. Os
repositórios que carregam uma cópia (ControleBancario, ControleRendaVariavel e
WealthfolioTeste) devem mantê-la alinhada a esta versão. Para mudar uma
definição, atualize o texto-base e as cópias na mesma rodada. O que é
particular de um sistema (telas, tabelas, comandos) fica na documentação dele.

O objetivo é **uma visão só** do dinheiro e do patrimônio. O ControleBancario
(CB), o ControleRendaVariavel (CRV) e o Wealthfolio medem partes diferentes da
mesma coisa; se usarem a mesma palavra para coisas diferentes, ou palavras
diferentes para a mesma coisa, o patrimônio aparece com duas verdades.

## Quem é dono de quê

| Assunto | Dono | Os outros |
|---|---|---|
| Todo movimento de dinheiro, em qualquer conta (inclusive de corretora), e os custos dele (corretagem, taxas, IRRF) | CB | CRV e Wealthfolio leem |
| Proventos, JCP, aluguel de ações e juros: o fato de caixa | CB | CRV lê o líquido; o Wealthfolio lê o bruto e as deduções |
| Papéis: quantidade, preço médio, opções, cotações, risco, resultado por operação | CRV | |
| Renda fixa, Tesouro Direto e caixa no exterior | CB | o CRV não os trata |
| A visão consolidada (patrimônio, retorno, alocação) | Wealthfolio | nenhum dos dois reconstrói a do outro |

Só a compra e a venda de um papel têm dois registros: a operação (CRV) e a
liquidação em dinheiro (CB). A conferência por pregão mostra a diferença; nenhum
lado grava no outro.

## Vocabulário

- **Receita e despesa gerencial** (CB, categoria `gerencial`): o que entrou ou
  saiu do caixa por motivo de resultado. Alimenta a **geração de caixa**
  (receitas menos despesas). Não inclui transferência, movimentação nem
  valorização.
- **Transferência**: dinheiro que muda de conta do mesmo titular. Não é
  receita nem despesa.
- **Movimentação** (CB): aporte ou resgate de investimento, como a liquidação de
  um pregão. O dinheiro sai do caixa e vira papel; não é despesa.
- **Valorização** (CB, categoria `valorizacao`, rótulo "Variação de mercado"):
  mudança de valor de renda fixa, Tesouro ou caixa no exterior, medida pelo saldo
  da conta. Não é receita nem despesa gerencial e não é fluxo de caixa. Para
  consumidores, é **retorno**: o contrato publica como `gerencial`, com
  `valuation: true` na categoria. No Wealthfolio o ganho entra como `CREDIT` e a
  perda como `FEE` (reduz o retorno sem ser aporte; não há tipo neutro negativo),
  sem categoria de gasto ou renda.
- **Rendimento** (CB): juros e rendimentos de contas e aplicações, que o
  extrato ou o saldo informado mostram. É receita gerencial.
- **Resultado realizado** (CRV): ganho ou perda de uma operação encerrada, em
  preço cheio, **sem custos**.
- **Variação não realizada** (CRV; antes "resultado aberto"): quantidade vezes
  (cotação menos preço médio) das posições abertas. A valorização do CB é a
  mesma ideia para os ativos que o CRV não trata.
- **Custos** (CB): corretagem, taxas e IRRF. O resultado líquido de uma
  operação é o do CRV menos esses custos.
- **Resultado total de investimentos** = renda + resultado realizado + variação
  não realizada − custos. **Só existe consolidado**: nenhum sistema sozinho
  tem todas as parcelas, e por isso nenhum deles exibe esse total.

## Regras que valem nos três

1. **Uma fonte declarada por medição.** O saldo de uma conta tem uma fonte
   (extrato, fatura, print, myProfit) e vale a declarada. Outra fonte para o
   mesmo dia é **conferência**: se divergir, vira pendência para decidir, e não
   um segundo ajuste. Trocar a fonte é um fato registrado.
2. **Reimportar é seguro, e a regra é a mesma.** Repetir a mesma fonte para a
   mesma data **substitui**; uma fonte diferente **acrescenta** e é conferida.
3. **Proventos: bruto, deduções e líquido.** O CB guarda o crédito bruto e as
   deduções (IRRF, taxa, corretagem do aluguel) como lançamentos separados, ligados
   pelo papel da linha (`income_role`). O líquido é o bruto menos as deduções: é o
   que o CRV mostra e o que entra no caixa. Os dois números descrevem o mesmo
   fato; nenhum deles é "o errado".
4. **Datas de medição.** Posição ao vivo (cotação) serve para decidir; a
   comparação entre sistemas e a visão consolidada usam o **fim do mês**, porque
   a renda fixa só é medida ali.
5. **Moedas não se somam.** Cada valor fica na moeda da conta ou do ativo; a
   conversão pertence a quem consolida.
6. **Ajuste assumido não é rendimento.** Diferença sem explicação entra com
   categoria e motivo próprios, para aparecer e poder ser revisada.
7. **Fonte dos dados.** Extratos e faturas para o dinheiro; o myProfit (que lê a
   B3) para negócios e posições; o RTD só para cotação ao vivo.
