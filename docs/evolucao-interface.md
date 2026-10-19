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

As linhas posteriores à carteira são direção para as próximas entregas, não
uma alteração já aplicada. Não é necessário converter todas as tabelas em
cartões: a apresentação acompanha a tarefa de cada tela.

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
