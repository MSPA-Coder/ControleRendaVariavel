# Evolução incremental — decisões de 18/10/2026

Manter Flask, SQLAlchemy, PostgreSQL e HTML/HTMX. O ganho desta rodada vem de
idempotência, transações explícitas, consultas com dono, tipagem de fronteiras,
testes reais e identidade do artefato; não há evidência para reescrever o domínio.
Não introduzir Repository genérico, microserviços, Redis ou broker.

## Fontes externas: medir antes de distribuir

O histórico Yahoo permanece sequencial, com o timeout existente por consulta.
Esse timeout não é um prazo máximo do lote: sete consultas lentas podem ocupar
o worker além dos 60 segundos configurados no Gunicorn. Também não é um
deadline absoluto de toda a resposta HTTP. Não se apresenta async como solução
automática, nem se acrescenta retry que multiplique o atraso.

Foram acrescentadas observações com relógio monotônico:

- por consulta: provider, outcome, duration_ms e quantidade de registros;
- por lote: entrada web/CLI, número de alvos/falhas/registros e duration_ms;
- categorias: ok, empty, timeout, rate_limited (429), http_error,
  connection_error, invalid_data e error inesperado.

Não registram tickers, donos, URLs, preços, corpo de resposta ou texto da exceção.
As regras de data/fuso/preço, exceções, timeout, ausência de retry e persistência
continuam iguais. Oito testes novos exercitam a fronteira de rede real substituída
por respostas/erros determinísticos e verificam a ausência de dados nos logs.

Amostra externa de desenvolvimento (não produção): três séries públicas,
sequenciais, responderam em **209,6 / 192,1 / 170,5 ms**, lote **572,5 ms**.
Isso confirma a coleta das métricas e o caminho de sucesso; não é p95, teste
de carga, SLA nem prova de disponibilidade futura do Yahoo.

Decisão atual: sem fila, concorrência adicional ou Circuit Breaker.
A atualização diária por CLI/timer já existe; não a duplicar. Para revisitar:
observar lotes representativos e erros por categoria, definir o tempo máximo
aceitável com o mantenedor e comparar com a duração/ocupação de workers.
Se importações interativas se aproximarem do limite de atendimento, priorizar
um orçamento de lote ou executar o comando existente como trabalho acompanhado.
Breaker só se falhas repetidas demonstrarem benefício; 429 deve respeitar a
limitação, não provocar retentativas em cascata.

RTD não foi alterado: já possui espera, timeout e separação de fonte/destino.
Não houve teste COM real nesta rodada.
