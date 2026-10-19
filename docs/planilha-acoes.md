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
| dias do ano | constante 365 |

O **delta da cotação** da planilha não tem correspondente: foi removido do
sistema em 23/09/2026 (revisão `20260923_0019`). Era um ajuste da planilha,
e aqui a cotação já é o preço do ativo; todas as posições usavam 1.

Corretoras e tickers têm cadastros próprios. Mercado, código RTD e moeda
pertencem ao ticker; posições apenas referenciam esses cadastros.

Um cadastro sem uso pode ser removido. Quando corretora, ticker ou carteira já
participa de posição, transação, provento ou extrato histórico, ele é
**arquivado**, não apagado: deixa de ser opção para novos lançamentos e segue
identificando corretamente o fato passado. A tela permite reativá-lo quando
for necessário voltar a operar com ele.

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

### Resultado no extrato expandido

Ao abrir o extrato de uma posição pelo **+** na aba **Ações**, a coluna
**Resultado** mostra, para cada **abertura** ou **aumento**, o ganho ou a perda
não realizado daquele lote contra a cotação atual:

```
resultado = sinal × quantidade do lote × (cotação atual − preço do aporte)
```

O sinal é `+1` para compra (`C`) e `-1` para venda (`V`). O valor é bruto (ver
"Resultado bruto" em **Fórmulas**). O resultado de cada linha usa
somente sua própria quantidade e seu preço de aporte, e não o saldo ou o custo
médio acumulado.

O resultado de um **encerramento parcial** continua sendo o resultado realizado
registrado no movimento. **Ajustes** não são compras nem vendas e permanecem
como não aplicáveis. Sem cotação atual, aportes também permanecem como não
aplicáveis. O resultado é calculado somente para a visualização: não
altera `PositionMovement.result` nem é persistida.

Todos os valores são um snapshot da cotação carregada na requisição. Uma nova
leitura do coletor pode atualizar a cotação logo depois, e o próximo refresh
recalculará os resultados; não há nova consulta por movimento.

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
- multiplicador do target e modo de resultado são os da
  posição existente: um aporte não redefine o alvo de uma posição em
  andamento;
- nenhum resultado é realizado.

Se a data de um novo lançamento for anterior à abertura registrada, o sistema
não o funde silenciosamente. Ele mostra um aviso e exige confirmação. Confirmado,
o novo lançamento passa a ser a **abertura**; a abertura anterior passa a ser
**aumento** e todo o extrato posterior é reaplicado cronologicamente, mantendo
baixas parciais e ajustes nos seus respectivos papéis.

Cada lançamento do extrato que seja abertura, aumento ou diminuição pode ser
editado pelo ícone de lápis. Alterar quantidade, preço ou data reaplica todo o
extrato em ordem cronológica e atualiza os saldos, o custo médio e a transação
espelhada de qualquer encerramento parcial. A lixeira remove aumentos e
diminuições, também reaplicando o extrato. A abertura também pode ser removida
quando o próximo lançamento cronológico for um aumento: ele passa a ser a nova
abertura e o extrato é reaplicado. Sem esse aumento, a abertura é removida pela
exclusão da posição inteira. Ajustes continuam sendo corrigidos pela edição da posição.
Se a edição de um aumento ou diminuição o mover para antes da abertura, o
sistema também pede confirmação: confirmado, esse lançamento passa a ser a
abertura e a abertura anterior passa a ser aumento antes do recálculo.

Regras de um **encerramento parcial**:

- a quantidade encerrada deve ser finita, positiva e não pode superar o saldo;
  o preço de saída deve ser finito e não negativo. Preço zero é permitido,
  tanto no encerramento parcial quanto no total;
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

## Comparação de cotações

No gráfico normal da aba **Cotações**, cada posição real de ações do usuário
no ticker selecionado aparece como **uma linha tracejada em degraus no custo
médio**. Ela começa na abertura, no custo médio inicial, e muda de nível na
data de cada movimento que alterou o custo (aumento ou ajuste manual), usando
o custo médio resultante gravado no extrato. Encerramento parcial não muda o
custo e não cria degrau. Posições abertas vão até a última cotação.

Posições encerradas também aparecem, em cinza: como o encerramento total apaga
o extrato, cada transação fechada vira um segmento reto no custo médio final,
da abertura ao encerramento. Um encerramento parcial é uma transação fechada
própria e gera o seu segmento. Opções não entram (o custo delas é prêmio, em
outra escala) nem a carteira Simulada. Ao aproximar o período, uma linha que
começou antes da janela aparece desde o início dela. A comparação com
benchmark permanece em evolução percentual e, portanto, não mostra
referências de preço absoluto.

Os gráficos de preço absoluto da aba **Cotações** e do detalhe da posição usam
eixo Y logarítmico: deslocamentos verticais iguais representam variações
percentuais iguais. A comparação com benchmark permanece linear porque o eixo
representa retorno percentual, que pode ser zero ou negativo. Uma referência
de custo ou aporte igual a zero não é desenhada, pois não existe em escala
logarítmica.

Os dois gráficos têm o mesmo seletor de período: 1 mês, 3 meses, 6 meses
(padrão), 1 ano, YTD e todo o período. A janela conta para trás a partir do
último fechamento; YTD começa em 1º de janeiro do ano desse fechamento.

No detalhe de uma posição aberta, "todo o período" são todos os fechamentos
desde a abertura. O gráfico tem eixos de preço e de data, e duas referências feitas só
com o extrato daquela posição: a mesma linha de custo médio em degraus e, mais
fina e pontilhada, uma linha por aporte (abertura ou aumento) no preço dele,
do primeiro fechamento a partir da data do aporte até o último.

Na aba **Cotações**, ao comparar dois ativos, o gráfico começa na primeira
data de calendário em que há cotação registrada para **ambos**. A data é a
primeira interseção exata dos históricos; não se usa a data de abertura da
posição nem se interpolam pregões ausentes. A partir desse ponto, cada série
mantém seus próprios dias registrados, e a listagem de cotações do ativo
selecionado continua exibindo o histórico completo. Se não houver nenhuma
data em comum, o gráfico não é desenhado e a tela informa explicitamente que
a comparação está indisponível.

## Resultado da posição com proventos

Na aba **Ações**, o seletor de **Resultado** oferece **Ação** (padrão),
**Proventos** e **Ação + Proventos**. O padrão pode ser omitido da URL; a
seleção é preservada nos filtros e nos refreshes HTMX.

Para uma posição aberta com cotação, a coluna **Resultado atualizado** usa o
modo selecionado:

```
R_ação       = sinal × quantidade atual × (cotação efetiva atual − custo médio)
R_proventos  = soma dos proventos pagos atribuídos à posição
R_combinado  = R_ação + R_proventos
Retorno acumulado = resultado selecionado / (quantidade atual × custo médio)
```

`R_ação` conserva o resultado bruto já definido em **Fórmulas**. O provento é
creditado na `payment_date`, como na Performance. Em cada data, o sistema
reconstrói a quantidade por posição a partir de `PositionMovement` e,
incluindo posições encerradas, de `PositionLedgerArchive`. Na `payment_date`,
conta a posição ao fim do dia: movimentos dessa data já foram aplicados e um
encerramento total nesse dia deixa quantidade zero para o rateio.
`Dividend.broker_id` não é usado para atribuir renda: o provento é do ticker e
o escopo segue os filtros escolhidos de carteira e corretora.

O rateio segue duas etapas. Primeiro, `prorate_dividends` atribui ao escopo a
fração das quantidades reais compradas do ticker nessa data, sobre a soma das
quantidades reais compradas, descartando o evento quando essa soma é zero.
Depois, o valor rateado ao escopo é distribuído entre as posições compradas
desse ticker no escopo, proporcionalmente à quantidade histórica positiva de
cada uma sobre a soma dessas quantidades. Posições encerradas participam
desse segundo denominador para que sua parcela não seja transferida às
posições restantes; somente posições atualmente abertas aparecem na grade.
Assim, uma posição só recebe provento pago numa data em que já detinha
quantidade comprada.

Somente eventos de **ações reais** entram no cálculo; contratos de opções e
carteiras simuladas não geram proventos atribuídos. Valores e rateios usam
`Decimal`, sem arredondar no domínio. As linhas sem cotação continuam com
resultado não aplicável e a indicação **Aguardando primeira cotação RTD**;
totais e agregados seguem a cobertura atual, que não soma linhas sem cotação.
O modo **Ação** preserva os resultados atuais. Em todos os modos, subtotais
por corretora, cartões por carteira/moeda e agregados por ticker somam o mesmo
resultado selecionado das linhas cotadas, sem misturar carteiras ou moedas.

## Performance mensal (retorno encadeado, TWR)

O relatório de performance (`app/routes/performance.py`,
`app/performance/monthly.py`) usa quantidade **histórica**, não a quantidade
atual da posição: para cada ticker, a última `resulting_quantity` do extrato
(`position_movements`/`option_position_movements`) com `occurred_on <= d`,
com sinal do `side`. As funções puras que implementam o modelo ficam em
`app/positions/holdings_history.py` (sem ORM, sem I/O — `HoldingEvent`,
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

**Rateio de proventos.** `Dividend` registra valores efetivamente recebidos e
não tem `portfolio_id`, só `broker_id` e `ticker_id`. O crédito é rateado entre
as quantidades compradas do ticker na data do pagamento, nunca por
`Dividend.broker_id` nem para posições vendidas:

```
L(data) = Σ max(quantidade_da_posição(data), 0)
D_recorte = D × L_recorte / L_total
```

O segundo rateio reparte o valor do recorte entre suas posições compradas,
proporcionalmente à quantidade histórica positiva de cada uma. Posições
vendidas não recebem crédito. O aplicativo não registra a obrigação de
repassar proventos nem os demais encargos da venda a descoberto; a apuração
oficial dessas operações continua sendo feita pelo sistema integrado à B3 e
pela contabilidade.

**Mês a mês.** Exposição líquida é a soma assinada das posições (V) no
último ponto do mês; não é o patrimônio total da conta, pois o app não mantém
saldo de caixa e não inclui ativos sem cotação histórica. Retorno é a
variação do índice TWR entre os pontos finais de dois meses; aporte líquido e
proventos são as somas dos fluxos dentro do mês.

O comparador de índice/benchmark (`build_benchmark_shadow_series`) usa a
mesma lista de fluxos reais, acumulando cotas (`cotas += F(d) /
preço_benchmark(d)`) em vez de ancorar num único aporte inicial — aporte e
retirada entram na data certa, inclusive quando `invested_amount <= 0`. O risco
por carteira (`app/performance/risk.py`,
`app/routes/risk.py`) usa o mesmo índice TWR como base do drawdown.

### Limitações aceitas

- **Crédito no pagamento.** `Dividend` guarda também `com_date`, mas não há
  campo específico de data ex. A Performance credita a renda em
  `payment_date`, conforme a regra atual do aplicativo; o preço e o crédito
  do provento podem cair em meses diferentes na série mensal.
- **A renda depende do cadastro.** A série de preços é nominal (não embute
  provento); uma renda não lançada é retorno que simplesmente não aparece.
- **Venda a descoberto.** O aplicativo não modela a obrigação de repassar
  proventos ao doador das ações nem encargos do aluguel. Portanto, a
  performance de posições vendidas permanece incompleta e não substitui a
  apuração oficial feita pelo sistema integrado à B3 e pela contabilidade. A
  B3 prevê o reembolso dos eventos corporativos pelo tomador ao doador no
  empréstimo de ativos ([regras da B3](https://www.b3.com.br/pt_br/produtos-e-servicos/solucoes-para-emissores/aumento-de-liquidez/emprestimo-de-ativos/)).
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

## Proventos importados e indicadores

A importação de proventos exige os cabeçalhos `Ativo`, `Corretora`,
`Recebido`, `Tipo`, `Data Pgto.`, `Data Com`, `YOC`, `DY`, `Cotas`,
`Total investido`, `Total atual`, `Preço Médio`, `Cotação` e `Valor por Cota`.
Pode haver colunas adicionais e a ordem não importa. Os totais e preços da
linha são snapshots do arquivo; o CRV recalcula o YOC e o DY históricos da
linha por eles, em pontos percentuais.

No resumo por ticker, os indicadores não somam esses percentuais históricos.
O YOC atual usa os proventos por ação pagos nos últimos 365 dias e o custo
médio ponderado atual das posições compradas reais abertas:

```
DY 12m    = soma dos proventos por cota pagos nos últimos 365 dias / cotação atual
YOC 12m   = soma dos proventos por cota pagos nos últimos 365 dias / custo médio atual por cota
```

O DY depende da última cotação disponível no CRV. O YOC não é exibido para
um ticker sem posição comprada real aberta. Na lista de cada lançamento,
Evento/custo continua mostrando o valor daquele pagamento dividido pelo total
investido informado; não é o YOC anual. Ambos os indicadores anuais são
informativos e não incluem valorização, impostos ou custos de negociação.

### Proventos do Controle Bancário (a partir de 01/01/2026)

Desde 18/10/2026 o dinheiro dos proventos é do Controle Bancário (CB). De
01/01/2026 em diante, dividendo, JCP e aluguel de ações **em reais**, nas
corretoras que o CB cobre (Genial e XP), vêm do CB (`origin = 'cb'`), não da
planilha. Duas regras de produto mudam em relação ao painel do myProfit:

* **O aluguel é o líquido do caixa.** A planilha e o myProfit mostram 57,5% do
  crédito bruto; o caixa recebe menos (corretagem de cerca de 30% e IRRF de 22,5%
  do bruto, mais taxas, líquidos de estornos). O CRV grava o líquido em `Recebido`
  e guarda o bruto e as retenções ao lado, exibidos na lista. Dividendo e JCP não
  têm retenção lançada: líquido = bruto. A série do aluguel cai cerca de 17% por
  crédito só por esta regra, e essa quebra de série é aceita.
* **O que sai da planilha.** O formulário e a importação por planilha recusam
  pagamento em reais dessas corretoras a partir do corte, com a mensagem de que
  o CB é a fonte. Dólar (Nomad), Avenue e as demais corretoras seguem na
  planilha. Até 31/12/2025 o histórico fica como está, e a tela o mostra como o
  subtotal "Até 31/12/2025 · saldo de corte".

Linha do CB não traz YOC, DY nem cotas; a tela as calcula como a importação da
planilha: cotas = quantidade comprada na corretora na data do pagamento; YOC =
recebido ÷ (cotas × custo médio atual); DY = recebido ÷ (cotas × cotação do
fechamento da data), em pontos percentuais. Valores calculados não são gravados.
O DY 12m e o YOC 12m do resumo por ticker usam essas cotas para os proventos do
CB. Detalhes de leitura, fila de revisão e substituição: `docs/architecture.md`.

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

Para `q` quantidade, `c` custo, `p` preço atual, `f` fechamento, `s = 1`
para compra e `-1` para venda:

| Saída | Fórmula |
|---|---|
| Atual | `delta * preço RTD` |
| Var. dia | `s * (p / f - 1)` |
| Bruto | `s * q * (p - c)` |
| Retorno acumulado | `Resultado selecionado / (q * c)` |
| Stop gain | compra: `c * m`; venda: `max(0, c * (2 - m))`, com `m` o multiplicador do target |
| Distância do target | `Stop gain / p - 1` |
| Breakeven | compra: `p/c - 1` quando `c < p`, senão `-(c/p - 1)`; venda: `1 - p/c` quando `p < c`, senão `c/p - 1` |
| Desmontar | `s * q * p` |
| Montar | `-s * q * c` |
| Peso atual | `abs(Desmontar) / soma(abs(Desmontar))` |
| Peso de custo | `abs(Montar) / soma(abs(Montar))` |
| Dias | `hoje - início` |

Divisões por zero produzem "não aplicável" (`None`), nunca erro nem infinito.

**A variação diária diverge da planilha, de propósito** (decisão de 15/09/2026):

- **Var. dia.** A planilha usa `s * (1 - f / p)`, que mede a variação contra o
  preço atual: fechamento 100 e preço 110 davam +9,09%, onde qualquer cotação
  mostra +10%. O sistema mede contra o fechamento. Vale também para as opções.

A coluna **Retorno acumulado** da aba Ações não anualiza: mostra o resultado
selecionado dividido pelo custo da quantidade ainda aberta. A anualização
permanece apenas na análise de risco, calculada sobre a série de preços.

**Resultado bruto** (decisão de 25/09/2026). O resultado é sempre
`s * q * (p - c)`, sem custos nem IR. A planilha tinha um modo `L`
("líquido"), e o sistema o reproduzia como `Bruto * 0,9996`. Esse fator vinha
de uma versão com bugs da `ResultadoOperacao`: o custo incidia sobre o
resultado, e não sobre o volume negociado; a corretagem nunca era cobrada; e,
numa perda, o "custo" deixava o número melhor. O modo saiu (revisão
`20260925_0021`). Custos reais e IR são apurados fora do sistema. Resultados
realizados gravados antes continuam com o valor calculado no encerramento.

**Stop gain e breakeven respeitam o lado** (mesma data). A planilha e o
sistema usavam a fórmula da compra também na venda: o alvo de uma venda com
`m = 1,5` ficava 50% acima do custo, onde ela perde. Na venda o alvo é
espelhado abaixo do custo e o breakeven é positivo quando o preço cai.

## Valores de referência

Estes casos fixam o resultado esperado da função de resultado e servem de
oráculo para os testes unitários de domínio:

| Entrada (lado, quantidade, custo, preço) | Resultado |
|---|---:|
| `C, 100, 10, 12` | `200,00` |
| `V, 100, 10, 12` | `-200,00` |
| `C, 1300, 14,20, 11,21` | `-3.887,00` |

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
- O filtro global de moeda vale para as telas financeiras. O padrão é BRL;
  USD restringe à moeda escolhida e `Todas` mostra ambos os grupos sem
  conversão nem soma implícita.
- Valores monetários e quantidades usam `Decimal`, nunca `float`.
- O extrato de uma posição explica seu estado atual: a quantidade da posição
  é a soma dos movimentos, e o custo médio é o do último deles.
- Um aumento de posição nunca realiza resultado; um encerramento parcial
  nunca altera o custo médio do saldo.
- O coletor mantém o provedor RTD aberto entre ciclos e o fecha nos intervalos
  ociosos (ProfitChart fechado, fora da agenda).
- Posições reais não são versionadas: são cadastradas pela interface, para
  manter dados pessoais fora do repositório.

## Exposição

Na aba **Ações** e nas análises de exposição, **posição bruta** é a soma dos
valores absolutos das ações com cotação. Ela não é o patrimônio total da conta:
não inclui caixa, opções ou posições sem cotação. A Performance mensal usa
**exposição líquida**, a soma assinada das posições cotadas, também sem caixa.

Os gráficos de alocação, corretora e mercado mostram, em cada item, o valor
atual e seu percentual, com títulos de coluna. Cada tela conserva as visões
separadas por moeda e, quando o recorte filtrado tiver BRL e USD, acrescenta
uma visão consolidada em USD: valores em BRL são divididos pela última cotação
histórica do ticker de referência `USDBRL=X` (BRL por USD). Como a importação
do Yahoo só grava dias encerrados, essa é a taxa de fechamento do último dia
importado, salvo lançamento manual mais recente. Sem essa cotação, o sistema
não mistura moedas e mantém apenas as visões separadas.

Posições sem primeira cotação permanecem visíveis nas três análises em uma
tabela própria, com a situação **Aguardando primeira cotação RTD**. Elas não
entram em valores nem percentuais e não geram grupos artificiais de valor zero.

Transações encerradas e proventos representam fatos realizados: suas datas de
abertura, encerramento e pagamento não podem estar no futuro. Cotações manuais
também não aceitam data futura e exigem preço estritamente positivo.
