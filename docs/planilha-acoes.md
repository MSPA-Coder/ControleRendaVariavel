# Contrato funcional: Ações

Este documento é a referência normativa das regras de ações implementadas
pelo sistema. Ele descreve o comportamento vigente; quando o código e este
documento divergirem, a divergência é um defeito de um dos dois e deve ser
resolvida explicitamente (ver a ordem de precedência em `AGENTS.md`).

A origem das regras é a aba `Ações` da planilha `Trades.xlsm`, mantida
apenas como referência de leitura: ela não é banco de dados, dependência de
runtime nem destino de escrita.

## Modelo

Uma posição é consolidada: existe no máximo uma por combinação de ticker,
corretora, tipo (C/V) e carteira. O mesmo ticker pode ter posições em
corretoras diferentes, em carteiras diferentes, ou uma compra e uma venda na
mesma corretora, mas não dois lotes da mesma exposição na mesma carteira.

Toda posição pertence a exatamente uma carteira (`Position.portfolio_id`);
é a carteira que decide se aquele lote é dinheiro real ou apenas um teste. Um
ticker pode estar associado a mais de uma carteira ao mesmo tempo
(`portfolio_tickers`, o cadastro N:N usado ao
lançar uma posição nova); a carteira da posição já existente, porém, não é
ambígua.

| Conceito | Campo do sistema |
|---|---|
| corretora | `broker` |
| carteira | `portfolio` |
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

## Movimentos da posição

Cada posição guarda o extrato dos lançamentos que a formaram
(`position_movements`), e a Carteira o abre pelo `+` ao lado do ticker
quando há mais de um.

| Movimento | Quando ocorre | Efeito na posição |
|---|---|---|
| abertura | primeiro lançamento do ativo | cria a posição |
| aumento | novo lançamento na mesma exposição | soma a quantidade e recalcula o custo médio |
| encerramento parcial | encerramento de parte da quantidade | reduz a quantidade; custo médio inalterado |
| ajuste | edição direta de quantidade ou custo médio | passa a valer o que foi editado |

Nada disso vale numa carteira `simulated`: ela existe só para ver valores nas
grades, não para operar. Uma segunda entrada na mesma exposição é **rejeitada**
em vez de fundida (não há aumento), não há extrato de movimentos, e não há
encerramento total nem parcial — desfazer é excluir a posição. Essas guardas
ficam no servidor (rota), nunca só na interface.

Regras de um **aumento**:

- o custo médio passa a ser a média ponderada pela quantidade,
  arredondada em oito casas (escala das colunas `Numeric(24, 8)`);
- a data inicial recua para a mais antiga entre a posição e o aporte, porque
  a exposição é mantida desde o primeiro lançamento;
- delta da cotação, multiplicador do target e modo de resultado são os da
  posição existente: um aporte não redefine o alvo de uma posição em
  andamento;
- nenhum resultado é realizado.

Regras de um **encerramento parcial**:

- o resultado realizado usa a mesma fórmula do encerramento total, aplicada
  à quantidade encerrada e ao custo médio vigente;
- o custo médio do saldo não muda: vender parte realiza resultado, não altera
  o que foi pago pelo que restou;
- em Transações, a operação produz duas linhas — uma fechada, com a
  quantidade encerrada e o resultado, e a linha aberta original reduzida ao
  saldo. As duas somam a quantidade anterior ao encerramento.

Encerrar a quantidade inteira é o encerramento total: a posição sai da
carteira e o extrato vai junto, porque o resultado realizado já fica
registrado em Transações, que sobrevive à posição.

Enquanto a posição de origem existir, a transação fechada de um encerramento
parcial **não é um registro independente**: ela espelha um movimento da
posição. Por isso, em Transações:

- ela não pode ser editada campo a campo — os valores deixariam de bater com
  o extrato e com a quantidade em carteira;
- excluí-la **desfaz** o encerramento: a quantidade volta para a posição, a
  baixa sai do extrato e os saldos dos movimentos seguintes são reaplicados.

Uma transação cuja posição de origem já não existe (encerramento total, ou
posição excluída depois) volta a ser um registro histórico comum, editável e
excluível sem efeito colateral.

## Performance mensal (retorno encadeado, TWR)

O relatório de performance (`app/routes/performance.py`,
`app/monthly_performance.py`) usa quantidade **histórica**, não a quantidade
atual da posição: para cada ticker, a última `resulting_quantity` do extrato
(`position_movements`/`option_position_movements`) com `occurred_on <= d`,
com sinal do `side`. As funções puras que implementam o modelo ficam em
`app/holdings_history.py` (sem ORM, sem I/O — `HoldingEvent`,
`QuantityTimeline`, `portfolio_flow_series`, `twr_index_series`).

**Fluxo, avaliado a preço de mercado.** A variação de quantidade em cada data
é multiplicada pela cotação daquela data, nunca pelo preço lançado no
extrato — `opened_on` de uma posição antiga costuma ser a data de cadastro,
não de compra, e `average_cost` é nominal enquanto o histórico de cotações é
ajustado; usar o preço lançado deixaria esses dois desalinhamentos
contaminar a série inteira de retorno.

```
F(d) = Σ [ q(t, d) − q(t, d−1) ] × P(t, d)
```

**Proventos** são creditados ao numerador do retorno, não ao patrimônio (o
app não tem conta caixa — o valor da carteira é só `Σ quantidade × preço`,
e um provento derruba o preço na data ex sem que o dinheiro recebido
apareça em lugar nenhum se não for tratado à parte):

```
r(d)      = (V(d) − V(d−1) − F(d) + D(d)) / |V(d−1)|      # None se |V(d−1)| == 0
índice(d) = índice(d−1) × (1 + r(d))
```

O `abs()` no denominador é o que faz posição comprada e vendida saírem
corretas pela mesma conta: o numerador é o resultado econômico, o
denominador é o capital empregado, e sem o valor absoluto o sinal do
retorno inverteria com `V` negativo (posição vendida).

**Rateio de proventos.** `Dividend` não tem `portfolio_id`, só `broker_id` e
`ticker_id` — um provento é rateado pela quantidade detida no recorte
filtrado sobre a quantidade real total do ticker naquela data
(`D_creditado = D × quantidade_no_recorte / quantidade_real_total`), nunca
por `Dividend.broker_id`. Quantidade total zero na data do pagamento
descarta o provento.

**Mês a mês.** `Valor` é o patrimônio real (`V`) do último ponto do mês;
`Retorno` é a variação do índice TWR entre os pontos finais de dois meses;
`Aporte líquido` e `Proventos` são as somas de `F` e `D` dentro do mês.

O comparador de índice/benchmark (`build_benchmark_shadow_series`) usa a
mesma lista de fluxos reais, acumulando cotas (`cotas += F(d) /
preço_benchmark(d)`) em vez de ancorar num único aporte inicial — aporte e
retirada entram na data certa, inclusive quando `invested_amount <= 0`. O risco
por carteira (`app/risk.py`,
`app/routes/risk.py`) usa o mesmo índice TWR como base do drawdown.

### Limitações aceitas

- **Data de pagamento, não data ex.** `Dividend` só guarda `payment_date`; a
  queda de preço acontece na data ex, então preço e crédito do provento
  podem cair em meses diferentes na série mensal.
- **A renda depende do cadastro.** A série de preços é nominal (não embute
  provento); uma renda não lançada é retorno que simplesmente não aparece.
- **Comprado e vendido na mesma moeda** podem levar `|V|` a perto de zero e
  produzir retornos explosivos — comportamento definido (`None` quando
  `V == 0`), sem tratamento especial além disso.
- **O resultado de mesmo dia não é capturado**: comprar a um preço e o dia
  fechar em outro é ganho real que o índice não registra, consequência
  direta de avaliar o fluxo a preço de fechamento em vez de preço de
  negócio.
- **`ADJUSTMENT` é datado com `date.today()`** — a data em que a correção
  foi feita, não a do fato corrigido. Corrigir hoje um erro antigo move a
  quantidade histórica só a partir de hoje; o patrimônio dos meses
  intermediários continua refletindo o valor anterior à correção.
- **Custo médio e `opened_on` podem ser imprecisos quando cadastrados em
  lote.** O relatório de performance não depende deles, mas Carteira,
  Transações e o resultado realizado dependem.

## Cotações RTD

ProgID: `rtdtrading.rtdserver`.

- Tópico B3: `TICKER_B_0`.
- Tópicos EUA: `TICKER_Y_0` e `TICKER_N_0`.
- `ULT`: última cotação, usada como snapshot persistido.
- `FEC`: fechamento anterior.
- `EST`: status do instrumento; a interface mostra o primeiro caractere.
- Para posições B3 com `EST` iniciado em `A` ou `L`, o valor atual usa o
  book: `OCP` para tipo `C` e `OVD` para tipo `V`. Nos demais estados e nos
  mercados `Y`/`N`, usa `ULT`.
- O preço de book escolhido é persistido somente como valor atual da posição
  aberta. O histórico de cotações continua recebendo `ULT`; bid e ask não
  ganham colunas nem tabelas próprias.
- Na grade de Ações e na de Opções, a coluna **ST** mostra o nome do estado
  (a letra sozinha não diz nada a quem não decorou a tabela) e o tooltip
  traz a explicação. Cada estado tem cor própria, porque o que se pode fazer
  com a posição muda junto:

  | `EST` | Estado | Mercado | Cor |
  |---|---|---|---|
  | `P` | Pré-abertura | calcula o preço de equilíbrio; aceita ordens, não executa | amarelo |
  | `A` | Aberto | negociação contínua; a ordem executa se houver contraparte | verde |
  | `L` | Leilão | saiu do túnel de preço ou está no leilão de fechamento | laranja escuro |
  | `F` | Fechado | pregão e leilões do ativo fora de curso | vermelho |
  | `S` | Suspenso | travado pela B3/CVM (fato relevante ou irregularidade) | roxo |

  A cor pinta a linha inteira nos estados em que não se opera normalmente
  (`P`, `L`, `F`, `S`); em `A` ela fica só no crachá da letra, senão a
  carteira ficaria verde o pregão inteiro e brigaria com o verde e o
  vermelho dos resultados. Letra desconhecida fica neutra.

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
| Retorno no período | `sinal(r) * ((1 + abs(r)) ** (período / d) - 1)` |
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

O período é selecionável como semanal (`7` dias), mensal (`30`), trimestral
(`90`), semestral (`182`) ou anual (`365`, padrão).

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
- Totais e pesos são agrupados por (moeda, natureza — real ou simulada, ver
  `Portfolio.simulated`); BRL e USD nunca são somados, e dinheiro simulado
  nunca é somado ao patrimônio real, mesmo dentro da mesma moeda.
- Pesos usam como denominador a carteira visível dentro do mesmo balde
  (moeda, natureza), mantendo denominadores independentes entre baldes.
- A grade pode ser filtrada por carteira e por corretora; o filtro vale para
  os totais e pesos exibidos. O padrão é "Todas" — linhas de todas as
  carteiras aparecem juntas, mas os totais continuam separados por balde.
- Valores monetários e quantidades usam `Decimal`, nunca `float`.
- O extrato de uma posição explica seu estado atual: a quantidade da posição
  é a soma dos movimentos, e o custo médio é o do último deles.
- Um aumento de posição nunca realiza resultado; um encerramento parcial
  nunca altera o custo médio do saldo.
- O coletor reutiliza uma única sessão Excel/RTD enquanto estiver em modo
  contínuo.
- Posições reais não são versionadas: são cadastradas pela interface, para
  manter dados pessoais fora do repositório.

## Exposição

Os gráficos de alocação, corretora e mercado mostram, em cada item, o valor
atual e seu percentual, com títulos de coluna. Cada tela conserva as visões
separadas por moeda e, quando o recorte filtrado tiver BRL e USD, acrescenta
uma visão consolidada em USD: valores em BRL são divididos pela última cotação
histórica do ticker de referência `USDBRL=X` (BRL por USD). Sem essa cotação,
o sistema não mistura moedas e mantém apenas as visões separadas.

Posições sem primeira cotação permanecem visíveis nas três análises em uma
tabela própria, com a situação **Aguardando primeira cotação RTD**. Elas não
entram em valores nem percentuais e não geram grupos artificiais de valor zero.

Transações encerradas e proventos representam fatos realizados: suas datas de
abertura, encerramento e pagamento não podem estar no futuro. Cotações manuais
também não aceitam data futura e exigem preço estritamente positivo.
