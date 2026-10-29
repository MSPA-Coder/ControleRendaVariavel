# Travas de banco

Inventário das travas (`with_for_update`, `pg_advisory_xact_lock`) que protegem saldo, posição e acesso, e de
**qual teste ou qual outra garantia** responde por cada uma. A regra está em `docs/TESTES.md` (T7 e T11): trava
nova nasce com teste de disputa que falha quando ela é retirada, ou com uma linha aqui dizendo qual garantia a cobre.

Medido em 29/10/2026 retirando cada trava numa cópia do commit e rodando os arquivos de disputa 3 a 5 vezes
(receita em T11). "Detectada" quer dizer que o teste falhou em todas as repetições sem a trava.

## Duas camadas, de propósito

`lock_executions(owner_id)` (`app/executions/locking.py`) serializa por dono tudo o que mexe em posição, e as
travas de dentro (linha da posição, chave da posição) valem para quem chega por outro caminho. Uma esconde a
outra: retirada sozinha, a de fora não é percebida porque a de dentro segura, e vice-versa. Por isso os testes de
`tests/test_travas_postgres.py` chamam o módulo interno (`positions.closure`, `options.closure`) direto, sem
passar por `use_cases`. Retirar as duas camadas juntas derruba os testes (ensaio de 29/10).

## Tabela

| Trava | Protege | Cobertura |
|---|---|---|
| `executions/locking.py` `lock_executions` | importação, aplicação e uso de caso por dono | `test_aplicacao_execucoes_concorrencia.py::test_operacoes_da_mesma_execucao_nao_duplicam_posicao[aplicar]` (detectada retirando a função) |
| `positions/closure.py` linha da posição (encerramento) | encerrar com a quantidade já alterada por outra transação | `test_travas_postgres.py::test_encerramento_espera_a_linha_da_posicao_e_usa_a_quantidade_nova[stock]` (detectada) |
| `options/closure.py` linha da posição (encerramento) | idem, opções | idem `[option]` (detectada) |
| `positions/closure.py` chave consultiva da posição + linha candidata à mesclagem | dois aportes na mesma chave não criam duas posições nem perdem quantidade | `test_mesclagem_espera_a_trava_da_chave_da_posicao[stock]` e `test_mesclagem_espera_a_linha_da_posicao_e_soma_sobre_a_quantidade_nova[stock]` (detectadas) |
| `options/closure.py` chave consultiva + linha candidata | idem, opções | idem `[option]` (detectadas) |
| `positions/closure.py`, `options/closure.py` linha da posição na reversão do encerramento parcial | reverter sobre a quantidade vigente | `test_reverter_encerramento_parcial_espera_a_linha_da_posicao[stock/option]` (detectadas) |
| `accounts/users.py` `_lock_admin_mutations` (advisory) | nunca ficar sem administrador ativo | `test_rebaixar_o_ultimo_administrador_espera_e_e_recusado` e `test_desativar_o_ultimo_administrador_espera_e_e_recusado` (detectada) |
| `accounts/users.py` `_locked_user` (linha do usuário) | alterar o usuário lido | **Coberta por** `_lock_admin_mutations`: os três chamadores (`update_user`, `reset_password`, `set_active`) tomam a trava consultiva antes. Retirada sozinha, nenhum teste falha (0/3), e não há caminho que mude usuário sem a trava consultiva. |
| `accounts/users.py` linha do usuário em `upsert_from_cli` | idem, no comando de linha | **Coberta por** `_lock_admin_mutations` (primeira instrução do método); o comentário no código diz o mesmo. |
| `routes/helpers.py` `owned_or_404_for_update` | ler e travar na mesma consulta | **Coberta por** `lock_executions`, tomada na mesma função para `Position`, `OptionPosition` e `Transaction`. Nenhuma rota chama a função com `Portfolio` ou `Dividend`; se alguma passar a chamar, essa linha vira a única trava e pede teste. |
| `positions/closure.py` e `options/closure.py` `_open_transaction_for` | linha aberta espelhada da posição | **Coberta por** a linha da posição e `lock_executions`: todos os chamadores (encerrar, editar, excluir) já seguram uma das duas. Retirada sozinha, nenhum teste falha. Sem teste próprio. |

## Sem proteção de teste

Nenhuma trava deste repositório está sem teste **e** sem garantia que a cubra. As quatro últimas linhas da tabela
dependem do raciocínio acima, não de um teste que falhe; se um chamador novo passar a usar essas funções sem
`lock_executions` nem a linha da posição, o raciocínio deixa de valer e a linha pede teste.
