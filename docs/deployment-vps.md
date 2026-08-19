# Implantação no VPS

O VPS executa somente a aplicação Flask e o PostgreSQL local ao Docker. O
ProfitChart, Excel/COM e o agente RTD permanecem no Windows. Não publique as
portas 5301 e 5302: o Nginx do host é o único componente exposto.

## Primeira publicação

1. Crie `.env.vps` a partir de `.env.vps.example`.
2. Crie `.secrets/secret_key`, `.secrets/postgres_password`,
   `.secrets/rtd_control_token` e `.secrets/collector_agent_token`. O último
   arquivo deve ser uma cópia segura do mesmo arquivo Windows. No Docker
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
