# Evolução da interface

Direção acordada em 19/10/2026: revisar o conjunto e entregar por etapas,
começando por ações e opções. A hierarquia dos menus permanece a mesma.

## Mapa das telas

| Área | Tarefa principal | Direção visual |
|---|---|---|
| Ações | Acompanhar as posições e aprofundar um trade | Cartões com preço, quantidade, custo, peso e resultado; detalhe da posição |
| Opções | Acompanhar contratos e prazos | Grupos por vencimento, cartões de contrato e detalhe com exposição e gregas |
| Transações | Localizar e conferir compras, vendas e encerramentos | Tabela enxuta, filtros e detalhe da operação |
| Proventos | Conferir recebimentos e sua origem | Resumo do período, tabela de eventos e detalhe; preservar a origem e a leitura exclusiva dos dados do CB |
| Conferência | Resolver divergências | Resumo de pendências, grupos por situação e contexto da divergência |
| Cotações e status | Ver preços, histórico e disponibilidade | Série histórica com seleção de ativo e estado explícito das fontes |
| Risco e performance | Interpretar exposição e evolução | Indicadores, gráficos e detalhamento das contribuições |
| Exposições | Entender a distribuição da carteira | Gráfico e lista de contribuições, preservando moeda e natureza |
| Cadastros | Localizar e manter referências | Tabelas compactas e formulários; evidenciar arquivamento e vínculos |
| Preferências e configurações | Ajustar escolhas pessoais e operação | Seções por finalidade, com ações administrativas identificadas |

As áreas do mapa têm implementação para validação local. Não é necessário
converter todas as tabelas em cartões: a apresentação acompanha a tarefa
de cada tela. A validação do conjunto antecede integração e implantação.

## Primeira entrega: carteira

O primeiro nível mostra o essencial de cada posição. As informações extensas
ficam na página própria do trade, em seções expansíveis de visão geral,
movimentos, metas/exposição e dados completos. Links e seções funcionam sem
JavaScript; os filtros e as cotações mantêm o contrato HTMX existente.

A página de ações mantém o modo de resultado escolhido na lista. O detalhe
identifica explicitamente o resultado bruto da ação; os pesos no detalhe têm
como referência a carteira da posição na mesma moeda. Opções mantêm o lado e
o tipo do contrato visíveis; o vencimento vem do cadastro mesmo sem cotação.

Resumo e agrupamento não fundem posições. Carteiras simuladas permanecem
identificadas, sem movimentos ou encerramento. Valores usam a formatação e
o modo discreto do sistema, e a aparência acompanha o tema escolhido.

Esta entrega não muda fórmulas, schema, dados persistidos ou processos RTD.
Os formulários de edição e encerramento continuam sendo os fluxos existentes.

## Segunda entrega: consultas da carteira

Transações passam a mostrar uma tabela de sete colunas, com o contexto do
ativo e o resultado realizado à vista. Datas, duração, carteira e observações
ficam em detalhes expansíveis; os indicadores das operações encerradas ficam
em uma seção própria. Operação aberta não apresenta resultado realizado.
Encerramento parcial continua vinculado à posição: desfazer preserva a
confirmação existente e devolve a quantidade pelo mesmo fluxo de domínio.

Proventos mostram totais por moeda e cartões por ativo, com total recebido,
DY e YOC de 12 meses. A composição por tipo e os eventos individuais são
acessíveis a partir do cartão. Cada evento preserva pagamento, data com,
líquido, bruto, retenções, indicadores, cotas e origem. Linhas do Controle
Bancário permanecem somente leitura. O histórico anual e o saldo de corte
continuam separados por moeda.

Conferência apresenta os pregões agrupados por situação, com os valores dos
dois lados, custo implícito e documentos. Diferenças aparecem primeiro. Sem
integração disponível, a tela declara a ausência de comparação; não apresenta
os pregões como aprovados nem como existentes somente no CRV.

Esta etapa mantém consultas, cálculos, filtros HTMX, permissões e ações nos
fluxos existentes. Não cria migrações, importações ou integração ativa no
ambiente de demonstração.

## Continuação: análise, mercado e manutenção

Risco passa a mostrar cartões por ativo, com volatilidade, retorno e drawdown
no primeiro nível; Sharpe, Sortino, VaR e Beta ficam no detalhamento. A
referência do Beta e a insuficiência da amostra permanecem explícitas.

Performance destaca os valores do último mês exibido em cada moeda,
preservando o cálculo existente. O gráfico precede a tabela mensal e a
metodologia fica acessível em seção expansível. O aviso sobre posições
vendidas continua visível. Exposições mostram gráficos e contribuições antes
da tabela de grupos; posições sem cotação continuam identificadas.

Cotações prioriza a série histórica e mantém os preços individuais por mês
em seção expansível. Status dos dados separa contagens e disponibilidade
da conversão. Não há atualização externa disparada por abrir essas telas.

Cadastros, preferências, configurações, usuários e formulários adotam os
mesmos cabeçalhos, espaçamento e superfícies. Inclusões em corretoras,
tickers, vencimentos e contratos ficam em seções próprias. As tabelas de
manutenção preservam edição, arquivamento e reativação. Confirmações,
validações, CSRF, papéis e a navegação restrita da troca obrigatória de senha
seguem nos fluxos existentes.

## Validação local antes de integração

O mantenedor valida a interface na máquina local antes de implantação. A
prévia usa o servidor de navegador do projeto com PostgreSQL descartável,
contas e posições sintéticas, projeto Compose e porta separados. Não usa o
volume `postgres_data`, segredos operacionais ou o coletor.

Dados operacionais não são copiados, importados, corrigidos ou migrados nesta
etapa. Qualquer ação sobre esses dados exige autorização específica do
mantenedor. Deploys devem ser coordenados para não executar em paralelo.

No checkout da mudança, subir somente a prévia:

```powershell
docker compose -p crv-interface-preview -f compose.browser.yaml -f compose.preview.yaml up --build -d browser-web
```

Acesso: <http://127.0.0.1:5382>, conta de teste `browser-demo`, com a mesma
senha sintética definida no bootstrap de navegador. Os nomes das carteiras
identificam os dados fictícios; as cotações são estáticas e podem aparecer
como desatualizadas. Edições e encerramentos afetam apenas esse cenário.

Para encerrar a prévia ou recriar o cenário, executar `down` com os mesmos
arquivos e nome de projeto. O banco é tmpfs: o cenário desaparece ao remover
o contêiner e é recriado na próxima subida. O protótipo estático inicial, na
porta 5381, permanece independente desta prévia funcional.

Durante a segunda validação, a prévia anterior foi preservada em 5382. A
segunda etapa usa outro projeto Compose, banco sintético e cookies próprios,
em <http://127.0.0.1:5383>. A configuração local está em
`.docker-local/preview-stage2.compose.yaml`, combinada somente com
`compose.browser.yaml`. A conta de demonstração é a mesma. Os proventos
marcados como CB são exemplos fictícios, sem consulta ao Controle Bancário.
Essa prévia pode recriar somente o serviço web mantendo o cenário já
existente: `BROWSER_REUSE_SCENARIO=1` permite isso exclusivamente no servidor
de navegador e em seu banco fixo descartável. A suíte automatizada mantém
a exigência de banco vazio. Nenhum cenário existente recebe novas sementes
durante um reinício da prévia.
