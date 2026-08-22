# Implantação no VPS

O VPS executa somente a aplicação Flask e o PostgreSQL local ao Docker. O
ProfitChart, Excel/COM e o agente RTD permanecem no Windows. Não publique as
portas 5301 e 5302: o Nginx do host é o único componente exposto, em
`https://renda-mspa.duckdns.org`.

O código no VPS é um espelho do `main`: toda mudança nasce na máquina de
desenvolvimento, vai ao GitHub e só então chega ao servidor. O servidor não é
lugar de editar código — `~/deploy.sh` recusa implantar se encontrar alteração
não commitada.

## Primeira publicação

O repositório é privado. O VPS o lê por uma *deploy key* somente-leitura,
registrada no GitHub em **Settings → Deploy keys** e apontada pelo apelido
`github-renda` em `~/.ssh/config`:

```
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
2. Crie `.secrets/secret_key`, `.secrets/postgres_password` e
   `.secrets/collector_agent_token`. O último arquivo deve ser uma cópia
   segura do mesmo arquivo Windows. No Docker
   Compose não-Swarm, deixe o diretório `.secrets` com modo `700` e os
   arquivos com `644`: PostgreSQL e Flask usam usuários Linux diferentes e
   ambos precisam ler a senha do banco; o diretório continua privado ao
   usuário de implantação.
3. Suba a pilha com:

   ```bash
   docker compose --env-file .env.vps -f compose.yaml up --build -d
   ```

4. Publique a configuração Nginx em `deploy/nginx/`, substituindo o domínio
   de exemplo. Primeiro emita o certificado com Certbot e depois use a versão
   HTTPS da configuração. Valide com `sudo nginx -t` antes de recarregar.
5. No Windows, instale o agente com
   `scripts/rtd-remote-agent.ps1 -Action Install -ApiUrl https://SEU-DOMINIO`.

## Atualização

A implantação é feita por `~/deploy.sh`, que confere a árvore, traz o `main`,
reconstrói a imagem, espera os health checks e valida o endereço público:

```bash
~/deploy.sh renda --check   # mostra o que mudaria, sem alterar nada
~/deploy.sh renda           # implanta
~/deploy.sh --status        # estado dos quatro projetos do VPS
```

O script aborta quando encontra alteração não commitada no servidor. Nesse caso
a correção é levar a mudança para a máquina de desenvolvimento, commitar e
enviar ao GitHub — nunca commitar no VPS.

`.secrets/` e `.certs/` não são versionados e vivem apenas no servidor. Os dados
ficam no volume `controle-renda-variavel_postgres_data`, fora da pasta do
código: substituir o diretório do projeto não os afeta. Não use
`docker compose down --volumes`.

## Verificações

```bash
curl -fsS http://127.0.0.1:5301/health
docker compose --env-file .env.vps -f compose.yaml ps
sudo certbot renew --dry-run --no-random-sleep-on-renew
```

Na interface de Configurações, salve o intervalo e a agenda desejados e use
**Atualizar cotações agora**. A chamada vai para o agente Windows, que envia a
leitura de volta para o VPS por HTTPS. O agente não faz chamadas ao VPS fora
da agenda nem quando o ProfitChart está fechado; pedidos manuais aguardam a
próxima janela ativa. Se o Windows/Profit estiver desligado, a aplicação
continua disponível e mostra a última cotação como desatualizada.
