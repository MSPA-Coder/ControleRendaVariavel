# Migração da política de testes — base 1.3 → 1.6

Instruções para propagar a política de validação e a classificação de testes
para outro projeto que use a mesma base compartilhada de engenharia.

**Como usar:** cole este arquivo no prompt de uma sessão aberta *dentro do
projeto-alvo*. Ele foi escrito para ser executado por um agente com acesso ao
código daquele repositório, não para ser aplicado de fora.

O projeto de referência é `ControleRendaVariavel`, onde a política foi
implementada e validada primeiro.

---

## O problema que a migração resolve

A base 1.3 exigia uma **validação de encerramento uniforme**: dez controles
(suíte completa, lint, tipos, cobertura, auditoria de dependências, imagem de
produção, smoke da pilha) para qualquer tarefa. Mover um botão custava o mesmo
que alterar uma migration — e a regra "qualquer alteração posterior invalida a
validação completa anterior" fazia todo ajuste tardio repetir tudo.

No projeto de referência isso dava ~4 minutos por tarefa. O rigor não estava no
lugar errado; estava distribuído de forma uniforme.

A 1.6 substitui o portão único por **anéis proporcionais ao raio da mudança**,
e reduz a classificação de testes a marcadores que algum comando seleciona.

---

## Etapa 1 — Atualizar o bloco da base compartilhada

O bloco entre `<!-- SHARED-ENGINEERING-BASE:BEGIN -->` e
`<!-- SHARED-ENGINEERING-BASE:END -->` é feito para ser copiado literalmente.

1. Copie o bloco inteiro do `AGENTS.md` do projeto de referência, substituindo
   o bloco existente no projeto-alvo. **Preserve os marcadores.**
2. Confirme que o atributo de versão ficou `version="1.6"`.
3. Confirme que o histórico de mudanças contém as entradas 1.4, 1.5 e 1.6.

Se preferir aplicar apenas o delta, as duas seções que mudaram são
**"Classificação de testes"** e **"Estratégia de validação progressiva"** —
elas substituem, respectivamente, "Classificação, manutenção e falhas
encontradas" e a antiga "Estratégia de validação progressiva". As entradas de
histórico a acrescentar:

```markdown
- **1.6** — Reduz a classificação de testes a marcadores que algum comando
  seleciona (`critical`, `security`, `smoke`), admite testes sem marcador e
  exige que um teste condicionado a dados semeie o cenário que verifica.
- **1.5** — Substitui o portão único de encerramento por anéis de validação
  proporcionais ao raio da mudança, move os controles de ambiente para CI e
  release, exige que os anéis sejam executáveis e trata a duração da suíte
  como propriedade do projeto.
- **1.4** — Reconhece o truncamento com restauração das linhas semeadas pelas
  migrações como mecanismo válido de isolamento entre testes, permitindo
  construir o schema uma única vez por sessão sem reexecutar as migrações a
  cada teste.
```

**Esta etapa é literal.** Não adapte o texto da base ao projeto: a base é
comum, e divergências entre projetos são exatamente o que ela existe para
evitar.

---

## Etapa 2 — Medir antes de mudar qualquer coisa

Não siga adiante sem números do projeto-alvo. As decisões seguintes dependem
deles, e o custo real costuma estar onde não se espera.

```bash
# quanto custa cada controle hoje
time <comando de lint>
time <comando de tipos>
time <comando da suíte completa>

# distribuição atual de marcadores (adapte a lista aos marcadores existentes)
pytest --collect-only -q -m <marcador>

# sobreposição entre marcadores — revela categorias redundantes
pytest --collect-only -q -m "<a> and not <b>"

# onde está o tempo: nos testes ou no setup?
pytest --durations=15
```

Interprete assim:

- Se nenhum teste isolado é lento mas a suíte é, **o custo está no setup**.
  Vá para a etapa 5.
- Um marcador com zero testes, ou cuja contagem "sem o outro marcador" é zero,
  é redundante e deve ser aposentado.

---

## Etapa 3 — Definir os anéis e torná-los executáveis

Uma política que depende de memória a cada tarefa não é política. Crie **um
comando por anel**.

| Anel | Quando | Conteúdo típico |
|---|---|---|
| 1 | a cada alteração | o menor teste que cobre o comportamento |
| 2 | antes de cada commit | lint + suíte rápida; suíte completa se o raio for amplo |
| 3 | antes do push | suíte completa + lint + tipos |
| 4 | CI e release | cobertura, auditoria, imagem de produção, smoke, E2E |

Use `scripts/check.ps1` do projeto de referência como template. **Adapte
obrigatoriamente:**

- os nomes dos serviços Compose (lá são `test` e `quality`);
- a lista `$wideReach` — os caminhos que podem quebrar qualquer coisa. No
  projeto de referência: models, migrations, `pyproject.toml`, `compose.yaml`,
  `Dockerfile` e `tests/conftest.py`. Ajuste aos arquivos equivalentes;
- o caminho dos testes unitários do anel 2;
- os caminhos que disparam `-m smoke` (templates e estáticos) e `-m security`
  (autenticação, sessão, fábrica da aplicação);
- a URL e a porta do health check no anel 4;
- se o projeto não roda em contêiner, troque as chamadas `docker compose run`
  pelos comandos diretos — o resto da estrutura vale igual.

Ganchos de Git versionados, para não depender de disciplina:

```bash
mkdir -p .githooks
# .githooks/pre-commit  -> chama o anel 2
# .githooks/pre-push    -> chama o anel 3
chmod +x .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
```

`core.hooksPath` é configuração local: documente no README que um clone novo
precisa rodar esse `git config`.

---

## Etapa 4 — Reclassificar os marcadores

**Não copie os marcadores do projeto de referência.** Eles descrevem aquele
código. Aplique o critério, não o resultado.

O critério é um só: **um marcador existe quando algum comando seleciona por
ele.** Três bastam:

| Marcador | Pergunta | Consequência |
|---|---|---|
| `critical` | pode custar dinheiro, corromper dados ou abrir acesso indevido? | roda primeiro, com falha rápida; nunca removido por volume |
| `security` | protege autenticação, autorização, CSRF ou sessão? | roda quando essas áreas mudam |
| `smoke` | verifica marcação, navegação ou renderização? | roda quando templates ou estáticos mudam |

Não formam partição: um teste de autorização é `critical` **e** `security`.
Testes que não se encaixam ficam **sem marcador** e rodam na suíte completa,
que é o padrão — ausência de marcador não é esquecimento, é a ausência de uma
seleção que precise distingui-los.

Procedimento, arquivo por arquivo:

1. Leia o que o teste realmente afirma, não o nome dele.
2. Pergunte as três perguntas da tabela. Marque o que responder "sim".
3. Aposente marcadores que nenhum comando seleciona.
4. **Procure classificações erradas enquanto migra.** No projeto de
   referência, dois casos apareceram: os parsers que convertem *preço* estavam
   como regra de negócio comum — um parse errado corrompe todo o resto, então
   são críticos; e a tela de configurações, que valida e persiste faixas
   numéricas, é integridade, não preferência.

Depois, declare os marcadores na configuração do pytest (`markers = [...]`) e
mantenha `--strict-markers`, para que um marcador inexistente falhe em vez de
passar silenciosamente.

---

## Etapa 5 — Atacar o custo da suíte

Só faça isto se a etapa 2 mostrou que o custo está no setup. Verifique as
pré-condições **antes** de aplicar cada item; aplicá-los às cegas quebra a
suíte.

### 5.1 Construir o schema uma vez por sessão

**Pré-condição:** a suíte recria o schema (migrações ou `create_all`) a cada
teste.

Troque por: schema construído uma única vez por sessão, e entre testes
reinicie apenas os *dados* — truncar tudo, restaurar as linhas que as próprias
migrações semeiam e realinhar as sequências.

Cuidado que custou tempo no projeto de referência: **migrações semeiam dados**
(linha de configuração singleton, tabelas de domínio). Truncar sem restaurar
faz testes falharem com "registro não encontrado". Capture o estado logo após
construir o schema e restaure a cada reset.

Testes que migram o schema de propósito (validam uma migração) precisam de uma
fixture que o reconstrua ao terminar.

### 5.2 Reaproveitar a aplicação entre testes

**Pré-condição:** a suíte cria a aplicação a cada teste.

Cada aplicação traz um engine e um pool de conexões novos. Uma instância por
sessão resolve — mas **restaure o estado mutável no teardown** (configuração e
classe do cliente de teste), senão um teste que autentica contamina os
seguintes. Testes que precisam de configuração diferente usam uma fábrica
separada.

### 5.3 Pré-compilar o SQL de reset

Se o reset consulta o catálogo do banco a cada teste para descobrir as
tabelas, monte esse SQL uma vez: a lista não muda durante a sessão.

### 5.4 Paralelizar

**Pré-condições:** o usuário do banco de teste tem `CREATEDB`, e a suíte não
compartilha estado mutável entre testes.

```sql
SELECT rolsuper, rolcreatedb FROM pg_roles WHERE rolname = '<usuário>';
```

Com `pytest-xdist`, dê **um banco por worker**, derivado da variável
`PYTEST_XDIST_WORKER` e criado sob demanda. Compartilhar um banco entre
workers quebra o isolamento: o truncamento de um apaga o que outro acabou de
semear.

Não fixe `-n` na configuração padrão do pytest — passe no comando do anel, para
que execuções de arquivo único não paguem a sobrecarga.

Resultado no projeto de referência: suíte completa de **192s → 76s** (5.1 a
5.3) **→ 32s** com 4 workers (5.4).

---

## Etapa 6 — Validar e registrar

1. Rode o anel 4 completo uma vez.
2. Confirme que o anel 2 realmente seleciona o que deveria: altere um template
   e verifique que o comando roda `-m smoke`, não os testes unitários.
3. Atualize o README com os comandos, os custos medidos e a instrução do
   `core.hooksPath`.
4. Atualize a CI para usar os mesmos comandos, com `critical` primeiro.
5. Commit único e coerente, citando os números antes e depois.

---

## Armadilhas observadas

- **Imagem que copia o código-fonte.** Se o serviço de teste não usa bind
  mount, rodar os testes sem `build` antes valida a versão anterior. Isso
  também morde ao contrário: `ruff --fix` dentro do contêiner corrige a cópia,
  não o host.
- **Teste que passa por vacuidade.** Um teste condicionado a dados que não
  foram semeados não verifica nada e ainda assim fica verde. No projeto de
  referência, foi assim que um filtro quebrado passou despercebido: a página
  só desenha o controle quando há registros, e o teste rodava com o banco
  vazio. A base 1.6 traz essa regra explícita — ao criar um teste condicionado
  a dados, semeie o cenário.
- **Marcador redundante.** Antes de aposentar um marcador, confirme que ele é
  mesmo redundante com `-m "<a> and not <b>"`. Se o resultado for zero, ele
  não distingue nada.
- **Não enfraquecer a base pela seção do projeto.** Se a política do
  projeto-alvo precisar divergir, registre como desvio aprovado na seção
  específica e explique o motivo — não reescreva o bloco comum.
