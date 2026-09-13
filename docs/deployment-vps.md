# Implantação no VPS

O VPS executa a aplicação Flask e o PostgreSQL em Docker. O Nginx do host é o
único componente exposto e publica `https://renda-mspa.duckdns.org`; as portas
5301 e 5302 permanecem em localhost. O ProfitChart e o agente RTD ficam no
Windows.

O código no servidor é um espelho do branch `main`. Mudanças nascem na máquina
de desenvolvimento, seguem para o GitHub e são implantadas pelo script
operacional. Não edite nem faça commit no VPS; `~/deploy.sh` recusa uma árvore
suja e a deploy key é somente leitura.

## Primeira publicação

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
   `.secrets/postgres_password`, `.secrets/collector_agent_token` e o material
   de `.certs/` exigido pelo build. Nunca registre ou exiba seus conteúdos. No
   Docker Compose não-Swarm, use modo `700` no diretório `.secrets` e `644` nos
   arquivos, pois PostgreSQL e Flask usam usuários Linux diferentes.
3. Suba a pilha:

   ```bash
   docker compose --env-file .env.vps -f compose.yaml up --build -d
   ```

4. Instale o vhost deste projeto a partir de `deploy/nginx/controle-renda-variavel.conf`
   (`sudo cp deploy/nginx/controle-renda-variavel.conf /etc/nginx/sites-available/controle-renda-variavel`
   e o link em `sites-enabled/`). Ele contém TLS e o HSTS, e depende dos dois
   arquivos verdadeiramente compartilhados entre os quatro projetos --
   `conf.d/00-comum.conf` (compressão, zona do limitador de login) e
   `snippets/proxy-app.conf` (cabeçalhos de proxy) --, mantidos em
   `../../_manutencao/vps/nginx/`, que precisam estar instalados primeiro. Valide com
   `sudo nginx -t` antes de recarregar, e confira com `sha256sum` dos dois
   lados que o arquivo do servidor é o que está versionado aqui (CRV-03: até
   02/09/2026 este era o único dos quatro projetos cujo vhost de produção
   existia só na memória do servidor, sem cópia versionada para restaurar
   numa recriação).
5. No Windows, instale o agente RTD com a URL HTTPS pública:

   ```powershell
   .\scripts\rtd-agent.ps1 -Action Install -ApiUrl https://renda-mspa.duckdns.org
   ```

O mesmo token do agente deve estar em `.secrets/collector_agent_token` nos dois
lados. O servidor apenas recebe chamadas HTTPS autenticadas; ele nunca tenta
alcançar o computador Windows.

`.env.vps` precisa trazer `FORCE_HTTPS=true` junto de `TRUST_PROXY_HEADERS=true`
-- a aplicação recusa subir com a segunda ligada e a primeira desligada
(CRV-03): confiar em `X-Forwarded-*` só faz sentido atrás de um proxy que
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
### Fluxo operacional usual

Use o script de implantação do VPS:

```bash
~/deploy.sh renda --check
~/deploy.sh renda
~/deploy.sh --status
```

Ele confere a árvore, atualiza `main`, reconstrói a imagem, aguarda os health
checks e valida o endereço público. Se detectar alteração local, corrija a
origem no ambiente de desenvolvimento e publique pelo fluxo normal.

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
