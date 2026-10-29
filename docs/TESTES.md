# Diretrizes de teste

Texto-base mantido em `manutencao/docs/TESTES.md`. Os repositórios que carregam
uma cópia devem mantê-la alinhada a esta versão; nem todo repositório tem uma
cópia. Para mudar uma regra, atualize o texto-base e as cópias existentes na
mesma rodada. O que é particular de um projeto (camadas, marcadores, comandos)
fica no `AGENTS.md` dele.

Base: os quatro pilares de Khorikov (*Unit Testing Principles, Practices, and
Patterns*), o *Test Desiderata* de Kent Beck, testar comportamento e não
implementação, *fitness functions* de arquitetura e teste de mutação.

## T1. Duas perguntas antes de existir

Um teste só entra se responder "sim" às duas:

1. Ele **falharia** se a regra estivesse errada?
2. Ele **continuaria passando** se o código fosse reescrito sem mudar o
   comportamento?

Só a primeira = detector de mudança: quebra a cada edição legítima. Só a
segunda = decoração. Os dois casos custam manutenção e não protegem nada.

## T2. Regra se testa pela porta pública, nunca pelo texto do código

Chame a função de domínio, o serviço ou a rota e confira o efeito: dado
gravado, resposta, recusa. **Procurar palavra no código Python é proibido**
(`'has_perm' in inspect.getsource(...)`, `'exigir_troca=False' in fonte`):
passa enquanto a palavra estiver escrita, mesmo que nunca seja alcançada. Foi
assim que seis rotas do ConfortoTermico ficaram sem controle de área (ver o
docstring de `test_autorizacao_por_area.py` naquele repositório).

## T3. Ler arquivo do repositório só como varredura de política

Legítimo quando a regra vale para **todos** os arquivos de um tipo e a
violação é um padrão proibido, com o motivo na mensagem de falha: nenhum JS
escreve `style` (a CSP bloqueia), nenhum `{# #}` ocupa duas linhas (vaza no
HTML), nenhum template busca host externo, todo input numérico tem marcador de
privacidade. Não vale conferir que **um** arquivo contém **um** trecho exato.

## T4. Configuração: conferir a propriedade, não o texto

Compose, CI, Dockerfile e `pyproject`: carregue o YAML/TOML e confira o valor
(`servico["read_only"] is True`). Melhor ainda, execute (`docker compose
config`, subir a pilha, smoke da CI). Trecho com indentação exata quebra ao
reordenar uma chave e não diz qual propriedade se perdeu.

## T5. Tela: o que o servidor decide, não a aparência

Testa-se:

- se o dado aparece ou não (permissão, dono);
- se o marcador de privacidade está no valor sensível;
- se a rota só de leitura recusa POST;
- se o erro devolve 4xx e mantém o formulário;
- o estado vazio contra o indisponível.

Quando o framework dá o contexto (`response.context` no Django), confira o
contexto em vez do HTML. Não se testa classe CSS, cor, fonte, ordem de coluna
ou rótulo de botão. Texto só quando **é** a regra (aviso legal, mensagem que
orienta a pessoa numa recusa). Aparência se verifica olhando a tela.

## T6. Front-end crítico: comportamento no navegador

Use o navegador para os comportamentos de JS em que a falha custa dinheiro ou
privacidade: confirmação que falha fechada, envio repetido, resposta HTMX antiga
que não sobrescreve a nova e modo discreto. Exercite a ação e confira a resposta
e o estado persistido. Posição, classe CSS e rótulo de botão não são resultados
de negócio. Os projetos que têm `compose.browser.yaml` já oferecem esse runner
com banco descartável e fontes externas controladas.

Quando o mecanismo ainda não puder ser executado no navegador, uma sentinela
textual temporária pode conferir o trecho que o define, desde que:

- seja marcado com `@pytest.mark.sentinela_front`;
- tenha no docstring o risco que protege;
- confira o mínimo que define o mecanismo, não cada linha dele.

Todo o resto que é cosmético sai. Migre a sentinela quando o teste de navegador
provar que detecta o mesmo defeito; não acumule os dois por rotina.

## T7. Banco real quando a regra vive no banco

PostgreSQL efêmero para:

- constraint, índice único, lock e concorrência;
- migração;
- SQL de data e fuso;
- contagem de consultas;
- atomicidade.

Regra de domínio pura roda sem banco. **Mock só na fronteira externa** (SMTP,
provedor de cotação, RTD, rede, relógio). Nunca mock de módulo próprio para
fugir do banco: congela a divisão interna, que é o que a refatoração muda.
SQLite não substitui o PostgreSQL.

**Trava de banco** (`select_for_update`, `with_for_update`, `pg_advisory_xact_lock`)
em fluxo de dinheiro, saldo, posição ou acesso nasce com um teste de disputa que
falha quando ela é retirada, ou com uma linha na documentação dizendo qual outra
garantia a cobre (constraint, ou outra trava mais externa). Como conferir: T11.

## T8. Migração que mexe em dado tem teste com dado

Revisão só de schema já é coberta pelo bootstrap (a cadeia inteira num banco
vazio). Revisão que transforma ou apaga dado ganha teste com o antes e o
depois; se ela pode abortar, o teste prova o aborto.

## T9. Regressão descreve o certo e prova que falhava

O nome diz a regra, não o bug ("leitura das 22h30 pertence ao dia local"). O
docstring conta o incidente em poucas frases. Antes do merge, mutação: o teste
precisa falhar no código antigo.

Teste de **tumba** (algo removido não volta) só quando a volta seria
perigosa. O admin do Django abria um segundo login sem trava de força bruta:
fica. Uma opção de filtro que saiu da tela: não fica.

## T10. Produção não ganha ramo para o teste

Configuração de teste entra por fixture ou injeção (o `config` da factory,
`settings` do pytest-django). É aceita a bandeira padrão do framework
(`TESTING`) se a produção se recusar a subir com ela ligada.

## T11. Densidade proporcional ao dano

Cobertura densa onde o erro custa dinheiro, dado ou acesso: saldo, fatura,
fechamento, posição, autorização, senha, backup. Smoke onde o erro é visível e
barato: telas de leitura, páginas institucionais.

Meça linhas e decisões e use as lacunas para escolher cenários relevantes.
Quando houver uma meta, como cobertura acima de 85%, informe também o escopo,
os módulos não executados e as camadas medidas. Não diminua o denominador,
adicione exclusões de código de produto ou escreva asserções vazias para
atingi-la. Execução não prova verificação: confira por mutação representativa
que as asserções novas detectam o erro que pretendem proteger.

**Mutação sob demanda, sem portão na CI.** `mutmut` 3.x e `pytest-cov` rodam na
imagem `quality`, inclusive com Django e PostgreSQL real. Configuração que
funcionou: `source_paths` com os módulos alvo, `pytest_add_cli_args_test_selection`
só com os arquivos de teste que os exercitam e `also_copy` com o que o `conftest`
precisa (o diretório `logs`, por exemplo). Selecionar a suíte inteira falhou
antes de mutar: mantenha a seleção enxuta e confira que ela roda. O conjunto de
sobreviventes muda de uma execução para outra: rode pelo menos 3 vezes e trate
como lacuna só o que sobrevive em todas. Borda de data, arredondamento e regra de
moeda são lacunas; o texto de uma mensagem de recusa não é (T5).

**Trava de banco.** Para saber se um teste protege um `select_for_update`, um
`with_for_update` ou um `pg_advisory_xact_lock`, retire a chamada numa cópia do
commit e rode a suíte com `-x`: saída 0 quer dizer que nenhum teste a protege.
Repita o arquivo de disputa algumas vezes, porque uma corrida que não ocorre numa
rodada engana, e rode também a suíte inteira: há trava que o arquivo de disputa
detecta sozinho e que a suíte inteira deixa passar. Um teste que só confere que a palavra da trava aparece no SQL não
protege nada (T2).

## T12. O que a documentação pode impor

**Pode:**

- descrever as camadas e o marcador de cada uma;
- dar o comando do portão;
- proibir desmarcar a camada com banco para o laço rápido passar.

**Não deve:**

- fixar a contagem de testes;
- listar quais arquivos formam uma camada (o marcador diz, e a lista apodrece);
- afirmar "ainda não tem teste" sem data;
- exigir teste para toda mudança, independentemente do risco.

## T13. Fluxo crítico: cinco cenários e frases confirmadas

Para cada fluxo que mexe em dinheiro, dado ou acesso, o conjunto mínimo é:

1. o caminho feliz;
2. o erro esperado (entrada inválida, regra recusada);
3. a repetição ou a simultaneidade: o mesmo envio duas vezes e duas requisições
   ao mesmo tempo, contra o PostgreSQL real (T7);
4. o limite (zero, máximo, virada de mês, saldo exato);
5. o acesso indevido (outro dono, perfil sem permissão).

As frases de comportamento ("quando X, o sistema faz Y") vão ao mantenedor antes
de virarem teste, e o nome do teste é a frase aprovada (T9). Vale só para
dinheiro, dado e acesso: não se aplica a telas de leitura (T11) e não exige teste
para toda mudança (T12).

## Diante de vermelho

Decida de quem é o defeito antes de mexer. Um teste que mede texto literal
reprova mudança legítima, e nesse caso quem se corrige é a asserção. Nunca
escreva código de produção para um teste passar. Se o teste estava certo, o
código está errado, mesmo que a mudança pareça inocente.

## Ao remover um teste

Confira por mutação que outro teste ainda pega o defeito que ele dizia
proteger. Se nenhum pega e o risco é real, reescreva em vez de remover.
