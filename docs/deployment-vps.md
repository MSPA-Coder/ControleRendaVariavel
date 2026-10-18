# Implantação no VPS

O VPS executa a aplicação Flask e o PostgreSQL em Docker. O Nginx do host é o
único componente exposto e publica `https://renda-mspa.duckdns.org`; as portas
5301 e 5302 permanecem em localhost. O ProfitChart e o agente RTD ficam no
Windows.

O VPS é `linux/arm64`. O job `Qualidade` usa runner ARM64 nativo e confere a
arquitetura das duas imagens antes de smoke, Chromium, Trivy e publicação.
Não se publica uma variante reconstruída e não testada para produção; o
desenvolvimento local AMD64 continua usando seu próprio build Docker.

O código no servidor é um espelho do branch `main`. Mudanças nascem na máquina
de desenvolvimento, seguem para o GitHub e são implantadas pelo script
operacional. Não edite nem faça commit no VPS; `~/deploy.sh` recusa uma árvore
suja e a deploy key é somente leitura.

## Primeira publicação

Para atualizações por `~/deploy.sh renda`, instale primeiro o script
correspondente de `_manutencao`. A CI de `main` constrói as imagens de
aplicação e PostgreSQL, executa smoke, Chromium e Trivy sobre esses artefatos e
publica os mesmos IDs, sem reconstruir. PRs não publicam. Os pacotes são
`ghcr.io/mspa-coder/controlerendavariavel` e
`ghcr.io/mspa-coder/controlerendavariavel-postgres`, com
`run-<run_id>-<tentativa>`.

O script exige o run completo `CI` bem-sucedido em `main` para o SHA exato,
confere revisão/run da imagem e resolve as duas tags para digest antes de
alterar o app. Usa `compose.release.yaml` com `--no-build --pull never`.
Registry/run inacessível ou identidade incorreta aborta sem alterar produção;
`--sem-ci` não dispensa essa identidade. O primeiro bootstrap abaixo continua
manual; não o use para atualizar uma instalação existente.

O GHCR cria pacotes privados por padrão. Antes da ativação, escolha a
visibilidade de ambos: públicos permitem pull anônimo; privados exigem acesso
de leitura no mecanismo de credenciais Docker do VPS, provisionado por canal
seguro. Nunca registre tokens em arquivos do projeto ou logs. Sem acesso
confirmado, não ative o deploy automático.

A rodada semanal da CI publica um novo run mesmo no mesmo SHA. O refresh do
VPS baixa essa imagem já testada; não reconstrói bases. O estado saudável em
`~/.local/state/mspa-deploy/controle-renda-variavel.images` guarda os digests.
Rollback restaura os IDs locais anteriores de web/migrate e
PostgreSQL/provisionamento, sem pull/build. Migrações/dados não são revertidos.

O serviço `db` declara `mspa.backup.postgres=true`, contrato de descoberta
do backup central mesmo quando a imagem é identificada por digest.

O repositório é clonado com a deploy key cadastrada no GitHub e o
apelido `github-renda` configurado em `~/.ssh/config`:

```text
Host github-renda
    HostName github.com
    User git
    IdentityFile ~/.ssh/deploy_renda
    IdentitiesOnly yes
```

```bash
git clone git@github-renda:MSPA-Coder/ControleRendaVariavel.git ~/apps/controle-renda-variavel
```

1. Crie `.env.vps` a partir de `.env.vps.example`.
2. Restaure por canal seguro `.secrets/secret_key`,
   `.secrets/postgres_password`, `.secrets/postgres_app_password`,
   `.secrets/collector_agent_read_token`,
   `.secrets/collector_agent_write_token`,
   `.secrets/patrimonio_integration_token` e o
   material de `.certs/` exigido pelo build.
   Nunca registre ou exiba seus conteúdos. No Docker Compose não-Swarm, use modo
   `700` no diretório `.secrets` e `644` nos arquivos, pois PostgreSQL e Flask
   usam usuários Linux diferentes. `postgres_password` fica reservado ao banco
   e às migrações; `web` e o agente RTD recebem somente `postgres_app_password`.

   O contrato v4 usa `patrimonio_integration_token`, gerado e rotacionado de
   forma independente. Entregue o valor ao consumidor por canal seguro.
   O `patrimonio_token` antigo deixou de existir no Compose em 03/10/2026
   (os contratos v1-v3 que ele autorizava foram retirados): um arquivo
   `.secrets/patrimonio_token` que sobrar no servidor não é lido por nada e
   pode ser apagado.
3. Suba a pilha:

   ```bash
   docker compose --env-file .env.vps -f compose.yaml up --build -d
   ```

4. Instale o vhost pelo instalador central do nginx, a partir de
   `_manutencao/vps/nginx/`: o arquivo `controle-renda-variavel` (TLS e HSTS),
   junto das peças compartilhadas `conf.d/00-comum.conf` (compressão e zona do
   limitador de login) e `snippets/proxy-app.conf` (cabeçalhos de proxy). O
   procedimento — backup, `nginx -t`, habilitação e conferência — está no
   `README.md` daquela pasta.
5. No Windows, instale o agente RTD com a URL HTTPS pública:

   ```powershell
   .\scripts\rtd-agent.ps1 -Action Install -ApiUrl https://renda-mspa.duckdns.org
   ```

O token de leitura deve estar em `.secrets/collector_agent_read_token` no VPS e
no Windows; o token de escrita deve estar em
`.secrets/collector_agent_write_token` nos dois lados. São segredos distintos:
o primeiro só lê configuração e o segundo só publica cotações/falhas. O
servidor apenas recebe chamadas HTTPS autenticadas; ele nunca tenta alcançar o
computador Windows.

`PATRIMONIO_OWNER_ID` também precisa apontar para o `id` do usuário financeiro
autorizado. `PATRIMONIO_TITULAR` é somente o nome da pessoa no contrato externo;
não substitui esse vínculo. O publicador recusa dados fora de
`PATRIMONIO_MAX_HISTORICO_DIAS` (dez anos por padrão).

**Conferência por pregão contra o CB (opcional, ainda não ligada em produção).**
Para a tela `/executions/reconciliation` e o comando `conferir-pregoes` lerem a
liquidação do Controle Bancário, o `web` precisa de `CB_PATRIMONIO_URL`
(`http://cb-api:8000/patrimonio/v4`, pela rede interna `patrimonio-internal` em
que o CRV já entra com o alias `crv-api`) e do segredo
`CB_PATRIMONIO_TOKEN_FILE`, uma cópia do `PATRIMONIO_INTEGRATION_TOKEN` do CB
(o CB tem um token só, e ele lê todas as contas: trate-o como senha de banco).
O `compose.yaml` ainda não declara esse segredo; acrescentá-lo é um passo de
operação separado, junto de conferir que `cb-api` está em `ALLOWED_HOSTS` do CB.
Sem isso a tela diz "CB não configurado" e nada mais é afetado.

**Proventos do CB (a partir de 01/01/2026).** Usam o mesmo `CB_PATRIMONIO_URL` e
o mesmo segredo, e só funcionam com o CB-1 em produção e preenchido (campos
`instrument`, `income_kind` e `income_role`). Configuração opcional:
`PROVENTOS_CB_CORTE` (padrão `2026-01-01`) e `PROVENTOS_CB_CORRETORAS` (padrão
`Genial,XP`). A ordem de operação, cada passo com autorização do mantenedor e o
primeiro com backup validado: `importar-proventos-cb` (ensaio, depois
`--aplicar`) e, só então, `substituir-proventos-planilha-2026` (ensaio, depois
`--aplicar`; `--desfazer` reverte). Nenhum dos dois roda sozinho no deploy.

O Compose é a mesma fonte de verdade no local e no VPS: `db-provision` cria ou
atualiza `investimentos_app` de forma idempotente antes do `migrate`, concede
apenas DML e uso de sequências e recusa DDL. O papel administrativo
`investimentos` não é montado no contêiner `web`. Bancos novos recebem
checksums por `POSTGRES_INITDB_ARGS=--data-checksums`; um volume existente só é
habilitado em janela controlada, depois de backup e ensaio de restauração.

`.env.vps` precisa trazer `FORCE_HTTPS=true` junto de `TRUST_PROXY_HEADERS=true`
-- a aplicação recusa subir com a segunda ligada e a primeira desligada:
confiar em `X-Forwarded-*` só faz sentido atrás de um proxy que
termina TLS, e sem `FORCE_HTTPS` o cookie de sessão sairia sem `Secure`.

## Topologia e rate limiting

O contêiner `web` usa Gunicorn com dois workers. Com o padrão
`RATELIMIT_STORAGE_URI=memory://`, cada processo mantém seu próprio contador e
o perde em reinícios. Por isso, o Nginx operacional de `_manutencao` é parte
obrigatória desta topologia: a zona compartilhada `login` limita apenas
tentativas `POST` em `/login`, sem limitar a abertura `GET` do formulário.

Se a aplicação for publicada sem esse Nginx, em múltiplas instâncias ou atrás
de outro edge, preserve proteção equivalente compartilhada ou configure um
storage compartilhado compatível para o limitador da aplicação. O contador em
memória dos workers não deve ser tratado como limite global.

## Atualização

### Publicação pelo GitHub

`main` é protegido e o VPS o espelha. Portanto, antes de chamar o script do
servidor, publique a mudança neste fluxo, a partir da máquina de
desenvolvimento:

```bash
git switch -c codex/minha-mudanca
# valide a mudança pelo portão quality
git add <arquivos>
git commit -m "Descreve a mudança"
git push -u origin codex/minha-mudanca
gh pr create --base main --head codex/minha-mudanca
gh pr checks <numero-do-pr> --watch
gh pr merge <numero-do-pr> --squash --delete-branch
git switch main
git pull --ff-only origin main
```

O push direto para `main` é recusado. Não inicie a implantação se algum check
obrigatório falhar: corrija a causa, valide novamente e atualize o mesmo PR.
Somente o `main` remoto já mesclado é elegível ao `~/deploy.sh`.

As três revisões abaixo já estão aplicadas em produção desde 13/09/2026. Os
roteiros continuam valendo para qualquer instalação que ainda esteja antes
delas — a local, por exemplo.

### Isolamento financeiro — revisão 20260912_0016

Esta revisão altera o contrato dos dados e requer janela de manutenção. Não
permita processos da versão antiga atendendo requisições ou gravando no banco
depois da migração. O procedimento abaixo é um roteiro de implantação; os
testes de desenvolvimento usam exclusivamente PostgreSQL descartável.

1. Valide um backup pelo fluxo do BackupRestore e ensaie sua restauração em
   ambiente separado. Registre contagens, valores e datas das carteiras,
   posições, opções, transações, proventos, movimentos e arquivos históricos.
2. Confirme o login exato `mspa` na cópia restaurada. Todos os registros
   financeiros legados serão atribuídos a essa conta, conforme decisão do
   mantenedor. Havendo legado sem esse login, a migração para antes de atribuir
   dados; não crie uma conta arbitrária para contornar a recusa. Banco vazio
   inicializa sem inventar um dono. Carteiras padrão personalizadas também
   exigem o proprietário confirmado.
3. Na janela autorizada, interrompa escritas e o envio do coletor e retire todos
   os workers web antigos. Aplique a nova imagem e `flask db upgrade` pelo
   serviço `migrate` do Compose operacional. Não use apenas uma troca gradual
   de workers: o código antigo não respeita o novo isolamento.
4. Compare o inventário financeiro antes/depois para `mspa`. Outras contas
   começam sem fatos financeiros legados. Corretoras e instrumentos continuam
   globais. Os snapshots antes repetidos por posição são consolidados por
   ticker/contrato, preservando a leitura mais recente e usando o ID da posição
   para desempatar timestamps iguais; a redução dessa contagem é esperada.
5. Confirme o health check e, com duas contas, a recusa de IDs alheios, a
   separação dos relatórios e das preferências e o acesso às cotações somente
   após posse atual ou histórica. Benchmarks seguem essa mesma regra.
6. Retome o coletor. O protocolo remoto continua usando IDs de posição, e o
   receptor resolve esses IDs para o instrumento global: isso preserva a
   compatibilidade com o agente existente. A coleta local que grava diretamente
   no banco deve usar o código novo. Verifique pulso, horários e atualização de
   ações e opções antes de reabrir o uso normal.

A revisão não oferece `downgrade` de dados. Se for necessário recuar, mantenha
o serviço fechado e restaure banco e código compatíveis a partir do backup
validado. Voltar somente a imagem antiga reintroduz leitura global indevida e
não constitui rollback seguro.

### Higienização do schema — revisão 20260913_0017

Esta revisão conclui a normalização anterior removendo cinco colunas obsoletas
em `positions`, o índice associado, e tornando obrigatórios timestamps já
exigidos pelos modelos. Antes de aplicá-la, valide backup e restauração pelo
BackupRestore. A revisão verifica que as colunas obsoletas estão vazias antes
de removê-las; se detectar qualquer valor, ela interrompe toda a transação sem
apagar schema ou dados. Nesse caso, mantenha o serviço fechado, investigue a
origem do valor e prepare uma migração explícita — não remova a pré-condição
nem altere a revisão publicada.

Os timestamps nulos recebem `CURRENT_TIMESTAMP` na própria transação antes da
restrição `NOT NULL`. A implantação habitual pelo `~/deploy.sh` aplica a
revisão; confirme depois `flask --app app:create_app db check` sem operações
pendentes e o health check público antes de encerrar a janela.

### Limpeza do coletor — revisão 20260913_0018

Esta revisão remove os campos sem efeito `collector_mode` e
`collector_destination`, seus tipos PostgreSQL e a rota de alternância já
desativada. Aplique somente depois de confirmar que a tarefa Windows usa
`scripts/rtd-agent-run.ps1 -Destination remote` e executa
`app.collector.remote_agent`; esse agente ignora o campo removido da resposta
de configuração. A revisão não usa `CASCADE` ao remover os tipos: uma
dependência inesperada interrompe a transação integralmente.

### Fluxo operacional usual

Use o script de implantação do VPS:

```bash
~/deploy.sh renda
```

Ele confere a árvore, o commit elegível no GitHub, atualiza `main`, reconstrói a
imagem, aguarda os health checks e valida o endereço público. `~/deploy.sh
--status` é apenas diagnóstico transversal, não uma etapa obrigatória do
deploy. Se detectar alteração local, corrija a origem no ambiente de
desenvolvimento e publique pelo fluxo normal.

Os dados financeiros e as configurações persistem no volume
`controle-renda-variavel_postgres_data`, fora do checkout. Não use
`docker compose down --volumes`. Backup, retenção e restauração são operados
exclusivamente pelo projeto BackupRestore.

## Verificações operacionais

```bash
curl -fsS http://127.0.0.1:5301/health
docker compose --env-file .env.vps -f compose.yaml ps
sudo nginx -t
sudo certbot renew --dry-run --no-random-sleep-on-renew
```

Na interface **Configurações**, salve a agenda e os intervalos e solicite uma
atualização. O agente Windows consulta a configuração e envia as leituras ao
VPS por HTTPS autenticado. Se Windows, ProfitChart ou agente estiverem
indisponíveis, a aplicação continua acessível e sinaliza cotações ausentes ou
desatualizadas.
