# Desenvolvimento e validação

O projeto usa Docker para aplicação, PostgreSQL, migrações, lint e testes. No
host são necessários apenas Docker Desktop, Git e um editor. A única exceção é o
agente RTD, que roda no Windows — ver a última seção.

## Ambiente

```powershell
Copy-Item .env.example .env
.\scripts\provision-secrets.ps1
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

`provision-secrets.ps1` cria, sem exibir os valores, `.secrets/secret_key` e
`.secrets/postgres_password` a partir de `SECRET_KEY` e `POSTGRES_PASSWORD` do
`.env` — **troque os dois valores de exemplo antes de provisionar** — e gera
`.secrets/collector_agent_token` aleatoriamente quando ele ainda não existe. Por
padrão o script recusa sobrescrever arquivo existente; `-Force` rotaciona, e
exige tratar a senha do banco e a invalidação das sessões abertas.

Faltam dois arquivos que o script não cria, porque não são segredos gerados
aqui:

- `.secrets/github_token.txt` — PAT somente-leitura, restrito ao repositório
  SharedAuth. O `pip install` do build o usa para baixar a dependência privada.
  Ele existe apenas durante o build, não vira variável de ambiente do runtime e
  não fica na imagem final;
- `.certs/local-root-ca.crt` — o Compose declara esse arquivo como secret, então
  ele **precisa existir** ou `docker compose config` falha antes de qualquer
  coisa. Numa máquina cujo antivírus ou proxy intercepta TLS, ele guarda a raiz
  a acrescentar ao truststore da imagem, sem a qual o `pip install` do build não
  valida a cadeia. Em máquina sem interceptação, um arquivo vazio basta:
  `New-Item -ItemType File .certs\local-root-ca.crt`.

Sem `compose.dev.yaml`, o Compose usa a imagem imutável, sem montar o código do
host — é essa a forma de validar o que de fato vai para produção. O serviço
`migrate` roda `flask db upgrade` e termina com sucesso antes de `web` iniciar;
`create_app()` só monta a aplicação e não consulta o banco.

A aplicação fica em <http://127.0.0.1:5301> e o PostgreSQL em `127.0.0.1:5302`.
O primeiro acesso precisa de uma conta:

```powershell
docker compose exec web flask --app app:create_app users create-admin
```

## Validação automatizada

O comando oficial executa Ruff e toda a suíte pytest na imagem `quality`:

```powershell
docker compose --profile quality run --build --rm quality
```

A imagem `quality` instala exatamente o que a imagem servida instala, mais as
ferramentas de teste — o que se valida é o ambiente que roda, não um arquivo de
requisitos.

**`--build` não é opcional.** O serviço `quality` não monta o código do host: o
que ele executa é o que foi copiado para a imagem. `docker compose run`
reconstrói apenas quando a imagem não existe — se ela já existe, o comando roda
a versão anterior do código e passa em verde sem ter visto nenhuma das suas
alterações. É uma falha silenciosa na direção pior: dá confiança sem dar
evidência. A CI não corre esse risco porque reconstrói sem cache antes de
executar; o comando local precisa do `--build` para ter o mesmo significado.

A suíte protege os contratos que valem para toda requisição: negação por padrão,
autorização por papel, CSRF, cabeçalhos e CSP, health check, resolução de
segredo, configuração de runtime, persistência dos filtros entre trocas de
fragmento, o endereço canônico da barra e a integridade do grafo de migrações.
Do domínio, ela cobre o que decide número na tela: quantidade histórica e fluxo
(`holdings_history`), redução mensal e TWR (`monthly_performance`), preservação
do extrato de posição encerrada (`position_ledger`), o coletor único e o agente
remoto.

**A suíte não toca o banco, e isso é desenho, não limitação.** Tudo o que ela
protege é decidido antes de qualquer consulta, e mantê-la sem banco é o que a
faz caber em segundos, sem infraestrutura de teste. Duas consequências
práticas, que valem mais do que qualquer contagem de casos:

- rodar `quality` **não** prova que o schema sobe. O bootstrap em PostgreSQL
  vazio continua sendo verificação manual obrigatória para toda mudança de
  schema;
- regra financeira nova deve nascer testável sem requisição e sem ORM. Uma
  regra que só possa ser exercitada com banco atrás fica fora da rede — o que é
  argumento para movê-la ao domínio puro, não para relaxar a suíte.

A CI valida o Compose, reconstrói a imagem `quality` sem cache, executa o
estágio, audita com `pip-audit` as dependências instaladas — pergunta diferente
da que o Dependabot responde, que é "saiu versão nova?" — e varre a imagem
servida com Trivy, cobrindo os pacotes de sistema que o `pip-audit` não vê. A
varredura roda em contêiner, com `docker save` e `--input`, sem montar o socket
do Docker.

Não afrouxe um controle para contornar achado: atualize a dependência ou a
imagem. Vulnerabilidade sem correção publicada sai por `--ignore-vuln ID` com um
comentário dizendo por quê — cada exceção vira uma decisão explícita e datada,
em vez de um vermelho permanente que se aprende a ignorar.

### Diante de um teste vermelho

Decida primeiro de quem é o defeito, e diga isso em voz alta:

- **do código** → corrija o código, nunca a asserção;
- **da asserção** → corrija a asserção, e a nova precisa ser mais forte ou mais
  precisa que a antiga, nunca mais permissiva;
- **do ambiente** → prove, comparando o mesmo comando contra o commit anterior,
  antes de descartar.

## Validação proporcional

Além do comando automatizado:

| Mudança | Validação adicional |
|---|---|
| documentação | links, caminhos, comandos, buscas residuais, `git diff --check` e `docker compose config --quiet` |
| rota, template ou HTMX | percorrer o fluxo afetado no navegador, com cenário real |
| cálculo financeiro | conferir contra os exemplos normativos de [`planilha-acoes.md`](planilha-acoes.md) ou [`planilha-opcoes.md`](planilha-opcoes.md) |
| autenticação, autorização, sessão ou CSRF | exercitar login, logout, uma operação mutante e a negação anônima |
| schema ou migração | backup validado, cadeia completa em PostgreSQL vazio, `upgrade` e `downgrade` da revisão alterada, health check |
| dependência, Dockerfile ou Compose | build sem cache quando pertinente, subida completa da pilha e `/health` |
| RTD | provedor determinístico e, quando disponível, um ticker conhecido, sem dado sensível no log |

Registre o que foi executado **e o que foi omitido**. Uma validação que não
aconteceu e não foi dita vira, na leitura seguinte, uma que aconteceu.

## Schema

O schema evolui somente por novas revisões em `migrations/versions/`. Não
reescreva revisão que possa ter sido aplicada, não use `create_all()` como
bootstrap e não use `stamp` no lugar de uma migração. Banco vazio nasce de
`alembic upgrade head`.

Gerar uma revisão exige o override de desenvolvimento: o serviço `web` roda com
filesystem raiz somente leitura e, sem o bind mount, o arquivo gerado não teria
onde ser gravado nem chegaria ao repositório.

```powershell
docker compose -f compose.yaml -f compose.dev.yaml run --rm web flask --app app:create_app db revision --autogenerate -m "descricao"
docker compose exec web flask --app app:create_app db upgrade
```

Revise toda migração gerada: o autogenerate não enxerga renomeação, migração de
dados nem constraint que dependa de conteúdo.

Os dados financeiros vivem no volume `postgres_data`. Nunca use
`docker compose down --volumes` fora de ambiente descartável. Backup, retenção e
restauração são responsabilidade exclusiva do BackupRestore; alteração
destrutiva de dados exige backup validado e autorização explícita.

## Trabalhar sem o RTD

Excel/COM não roda no contêiner Linux, então o caminho real de coleta não existe
no ambiente de desenvolvimento. Isso não bloqueia nada: **sem o agente, a
aplicação continua utilizável**, as cotações aparecem indisponíveis ou
desatualizadas e nenhum cadastro depende delas.

Para exercitar a coleta, use os provedores determinísticos da suíte em vez de
COM. `poll-rtd` e `probe-rtd-direct` dependem de Excel e só rodam no ambiente
Python isolado do Windows — não os execute no contêiner `web`.

Para gravar no PostgreSQL local, a tarefa única pode ser instalada no logon do
Windows por `scripts/rtd-local-agent.ps1 -Action Install`. Ela executa
`poll-rtd --watch` de forma invisível e termina com o logoff do Windows. Esse
é um ciclo do sistema operacional, não da sessão web; não se deve iniciar ou
parar o processo a cada login/logout HTTP. Não instale a tarefa local junto do
agente remoto para o mesmo ProfitChart. O comando `poll-rtd` também possui
exclusão interprocesso, cobrindo o toggle administrativo e múltiplos workers.

O caminho do agente remoto (`REMOTE_COLLECTOR_ENABLED=true`) pode ser exercitado
sem Windows chamando os endpoints `/api/collector/*` com o Bearer token de
`.secrets/collector_agent_token`: é a mesma superfície que o agente usa, e a
única que ele tem.
