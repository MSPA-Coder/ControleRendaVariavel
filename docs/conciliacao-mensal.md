# Conciliação mensal com o myProfit

O myProfit lê a B3 e é a fonte dos negócios e da posição dos ativos da bolsa
(ações, opções e ETF). O CRV guarda a carteira, o preço médio e o resultado por
operação. Uma vez por mês, cada lado é conferido contra o outro, e o que não
bate vira tarefa. Nada é gravado nas posições automaticamente: a conciliação
**mostra** a diferença; corrigir é decisão de quem mantém.

O que **não** entra: futuros, Tesouro Direto, renda fixa e contas no exterior
(Avenue, Nomad, Revolut). O IR continua no myProfit; o CRV não o calcula.

## Quando e quem

Todo primeiro dia útil, para o mês que fechou. A rotina exige o titular logado
no myProfit, no navegador embutido do Claude Code (nenhuma senha é guardada em
lugar algum), e acesso SSH ao VPS1. O lembrete agendado abre a sessão que a
executa.

## Passos

1. **Extratos do CB.** Importe no Controle Bancário os extratos do mês das contas
   da Genial e da XP. Sem eles, o `conferir-pregoes` mostra os últimos pregões
   como "só no CRV".
2. **Emitir os relatórios** (aba `https://myprofitweb.com/App/...` já logada):
   carregue `scripts/myprofit/ler-relatorio.js` no console e rode, com as datas do
   mês (a posição usa o último dia):

   ```js
   const negocios = await lerRelatorio(0, 'Histórico de investimentos', '2026-10-01', '2026-10-31');
   const posicao  = await lerRelatorio(11, 'Posição detalhada', '2026-10-01', '2026-10-31');
   linhasEmTsv(negocios.linhas); linhasEmTsv(posicao.linhas);
   ```

   O site entrega um `.xlsx` real, mas o botão usa `window.open`, que o navegador
   embutido bloqueia; por isso o script lê o arquivo com `fetch` e devolve o TSV.
   As duas primeiras linhas do relatório trazem **nome e CPF do titular**:
   `linhasEmTsv` os troca por um texto fixo. Nunca imprima nem grave o original.
   (O relatório "Operações finalizadas" repete nome e e-mail em **cada linha**;
   não é usado aqui e, se for lido para conferência manual, só as colunas de
   ativo, quantidade, preço e resultado podem ser mostradas.)
3. **Levar ao contêiner.** O sistema de arquivos do `web` é somente leitura;
   `/tmp` é tmpfs. Mande cada TSV pela entrada padrão:

   ```bash
   ssh ubuntu@VPS1 'docker exec -i controle-renda-variavel-web-1 sh -c "cat > /tmp/negocios.tsv"' < negocios.tsv
   ssh ubuntu@VPS1 'docker exec -i controle-renda-variavel-web-1 sh -c "cat > /tmp/posicao.tsv"' < posicao.tsv
   ```

4. **Importar** (cada comando faz o ensaio e só grava com a opção):

   ```bash
   flask --app app:create_app importar-negocios-myprofit /tmp/negocios.tsv --gravar
   flask --app app:create_app importar-posicao-myprofit /tmp/posicao.tsv --gravar
   ```

   Reimportar é seguro: negócios repetidos são reconhecidos (documento, ordem da
   linha e `content_hash`) e a posição da mesma data é substituída. Apague os TSV
   de `/tmp` no fim.
5. **Conferir.**

   ```bash
   flask --app app:create_app conferir-pregoes --inicio 2026-10-01 --fim 2026-10-31
   flask --app app:create_app conciliar-myprofit
   ```

   `conferir-pregoes` compara o dinheiro (liquidação do CB contra as execuções) e
   `conciliar-myprofit` compara o papel. Este sai com código 1 quando há
   pendência. Por padrão as vendas valem do 1º de janeiro até hoje, para que um
   erro antigo continue aparecendo até ser resolvido.

## Lendo o resultado

`conciliar-myprofit` tem duas seções.

**Posições** compara quantidade e custo médio da posição do myProfit (somada por
ativo) com as posições abertas do CRV (somadas por ativo, sem a carteira
Simulada). O custo médio tolera 0,1%: o myProfit guarda quatro casas, o CRV oito.

**Vendas** compara a quantidade vendida nas execuções do período com a quantidade
fechada nas transações do CRV no mesmo período. É ela que acusa o ciclo inteiro
que ficou de fora do CRV (compra e venda dentro do período, sem posição aberta
para a comparação de posição enxergar).

| Estado | O que significa | O que fazer |
|---|---|---|
| Confere | os dois lados batem | nada |
| Quantidade | o mesmo ativo, quantidades diferentes | achar o lançamento que falta ou sobra (compra, venda, atribuição) e corrigir no CRV |
| Custo médio | quantidade igual, custo diferente | conferir o preço dos lotes; o CRV usa preço sem taxas, o myProfit usa com taxas, e isso só explica diferença pequena |
| Só no myProfit | o CRV não tem o ativo | registrar a posição |
| Só no CRV | o myProfit não tem | normalmente é ativo que o myProfit ainda não integrou (a integração tem atraso); confirmar |
| Só nas execuções | houve venda e o CRV não tem a operação | reconstruir o ciclo com os relatórios do myProfit |
| Reconhecida | diferença explicada e anotada | nada |

Diferença que **nunca vai bater por desenho** é anotada, e deixa de contar como
pendência, mas continua aparecendo com a nota:

```bash
flask --app app:create_app reconhecer-conciliacao venda RAIZO700 "atribuição das puts: o prêmio já está no custo médio do RAIZ4"
flask --app app:create_app reconhecer-conciliacao venda RAIZO700 --remover
```

Exemplo real de 18/10/2026: as vendas de RAIZO600 e RAIZO700 em fevereiro abriram
puts vendidas; a recompra de 20/03 foi atribuição (45.000 ações), e o CRV já tem
essas ações com o custo líquido do prêmio. Registrar as opções contaria o prêmio
duas vezes.

## Corrigir o que falta

Registre pelo caso de uso da própria tela (`register_stock`, `close_stock`,
`register_option`, `close_option`), com o preço **sem taxas**. Antes de gravar
dado real: backup validado pelo BackupRestore. Para ativo que o CRV nunca teve,
reconstrua o ciclo inteiro (abertura, parciais e fechamento) a partir do
"Histórico de investimentos" do ano da compra, e confira o resultado contra o
relatório "Operações finalizadas" do myProfit (a diferença esperada são as taxas).
A opção que o relatório não descreve (strike e vencimento) se infere do código e
é conferida com o mantenedor.

## Limites conhecidos

- A posição do myProfit é a da data do relatório; lançamentos do CRV feitos depois
  dela aparecem como diferença. Emita a posição no último dia do mês e concilie
  logo em seguida.
- O myProfit não traz corretora na posição; a comparação é por ativo, e uma
  diferença de corretora (mesmo ativo, quantidade trocada entre duas) não aparece.
- Compra sem venda correspondente de ativo que já saiu da carteira por outro
  caminho (exercício, vencimento sem valor) não gera execução de venda: aparece
  como diferença de posição ou fica fora, e se anota.
- A integração do myProfit com a Avenue tem atraso; por isso o exterior está fora.
