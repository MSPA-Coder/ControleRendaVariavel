# Implantação no VPS

O VPS executa a aplicação Flask e o PostgreSQL em Docker. O Nginx do host é o
único componente exposto e publica `https://renda-mspa.duckdns.org`; as portas
5301 e 5302 permanecem em localhost. Excel, ProfitChart e o agente RTD ficam no
Windows.

O código no servidor é um espelho do branch `main`. Mudanças nascem na máquina
de desenvolvimento, seguem para o GitHub e são implantadas pelo script
operacional. Não edite nem faça commit no VPS; `~/deploy.sh` recusa uma árvore
suja e a deploy key é somente leitura.

## Primeira publicação

O repositório privado é clonado com a deploy key cadastrada no GitHub e o
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
   .\scripts\rtd-remote-agent.ps1 -Action Install -ApiUrl https://renda-mspa.duckdns.org
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
