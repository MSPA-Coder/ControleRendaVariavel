# Diretrizes de teste

Cópia de `_manutencao/docs/TESTES.md`: mude lá e rode
`compartilhados/sincronizar.py --aplicar`. O que é próprio de um projeto
(camadas, marcadores, comando) fica no `AGENTS.md` dele. A numeração T1–T16 é
citada em testes e documentos: regra nova ganha o número seguinte e nenhuma
muda de número.

## O que merece teste

**T1.** Um teste entra se **falharia** com a regra errada e **continuaria
passando** com o código reescrito sem mudar o comportamento. Sem a primeira, é
decoração; sem a segunda, é detector de mudança, que quebra a cada edição
legítima.

**T11.** A densidade acompanha o dano. Cobertura densa onde o erro custa
dinheiro, dado ou acesso: saldo, fatura, fechamento, posição, autorização,
senha, backup. Smoke onde o erro é visível e barato: telas de leitura, páginas
institucionais. Mudança sem risco não precisa de teste novo.

**T13.** Fluxo que mexe em dinheiro, dado ou acesso cobre cinco casos: o
caminho feliz; o erro esperado; a repetição e a simultaneidade (o mesmo envio
duas vezes, duas requisições ao mesmo tempo, contra PostgreSQL real); o limite
(zero, máximo, virada de mês, saldo exato); e o acesso indevido (outro dono,
perfil sem permissão). O nome do teste é a frase de comportamento ("quando X,
o sistema faz Y"), e as frases novas aparecem no resumo da entrega.

## Como testar

- **T2. Pela porta pública.** Chame a função de domínio, o serviço ou a rota e
  confira o efeito: dado gravado, resposta, recusa. Procurar palavra no
  código-fonte é proibido: passa enquanto a palavra estiver escrita, mesmo que
  nunca seja executada. Foi assim que seis rotas do ConfortoTermico ficaram sem
  controle de área.
- **T3. Ler arquivo do repositório** só como varredura de política sobre todos
  os arquivos de um tipo (nenhum JS escreve `style`, nenhum template busca host
  externo), com o motivo na mensagem de falha. Conferir que um arquivo contém
  um trecho exato não vale.
- **T4. Configuração** (Compose, CI, Dockerfile, `pyproject`): carregue o YAML
  ou o TOML e confira a propriedade, ou execute (`docker compose config`, subir
  a pilha). Trecho de texto quebra ao reordenar uma chave.
- **T5. Tela:** teste o que o servidor decide. Se o dado aparece (permissão,
  dono), o marcador de privacidade, rota de leitura que recusa POST, erro com
  4xx que mantém o formulário, vazio × indisponível. Prefira o contexto do
  template (`response.context`) ao HTML. Classe CSS, cor, ordem de coluna e
  rótulo de botão não se testam; texto só quando ele é a regra.
- **T6. Navegador** (`compose.browser.yaml`, onde existe) para JS cuja falha
  custa dinheiro ou privacidade: confirmação que falha fechada, envio repetido,
  resposta HTMX antiga que não sobrescreve a nova, modo discreto. Sem como
  rodar no navegador, vale uma sentinela textual mínima marcada com
  `@pytest.mark.sentinela_front` e o risco no docstring; ela sai quando o teste
  de navegador pegar o mesmo defeito.
- **T7. Banco real quando a regra vive no banco:** constraint, índice único,
  lock, concorrência, migração, SQL de data e fuso, contagem de consultas,
  atomicidade. Regra pura roda sem banco. Mock só na fronteira externa (SMTP,
  cotação, RTD, rede, relógio), nunca em módulo próprio para fugir do banco.
  SQLite não substitui o PostgreSQL. Trava de banco (`select_for_update`,
  `with_for_update`, `pg_advisory_xact_lock`) em fluxo de dinheiro, saldo,
  posição ou acesso nasce com um teste de disputa que falha sem ela, ou com uma
  linha na documentação dizendo qual outra garantia a cobre (como conferir: T14).
- **T8. Migração que transforma ou apaga dado** ganha teste com o antes e o
  depois, e com o aborto se ela puder abortar. Mudança só de schema já é
  coberta pela cadeia inteira aplicada a um banco vazio.
- **T10. Produção não ganha ramo para teste:** configuração de teste entra por
  fixture ou injeção. A bandeira `TESTING` do framework é aceita se a produção
  se recusar a subir com ela ligada.

## Provar que o teste protege

- **T9. Regressão:** o nome diz a regra, não o bug; o docstring conta o
  incidente em poucas frases. Antes do commit, rode o teste com a correção
  desfeita: ele precisa falhar. Teste de tumba (algo removido não volta) só
  quando a volta seria perigosa.
- **T14. Trava de banco:** para saber se um teste a protege, retire a chamada
  numa cópia e rode o arquivo de disputa algumas vezes e a suíte inteira com
  `-x`: saída 0 quer dizer que nada a protege. Teste que só procura a palavra
  da trava no SQL não protege nada (T2).
- **T15. Mutação** é sob demanda, sem portão: `mutmut` 3.x e `pytest-cov` rodam
  na imagem `quality`. Configuração que funciona: `source_paths` com os módulos
  alvo, `pytest_add_cli_args_test_selection` só com os testes que os exercitam
  e `also_copy` com o que o `conftest` precisa. Rode pelo menos 3 vezes; lacuna
  é só o que sobrevive em todas.
- **T16. Cobertura:** meta vem com o escopo e os módulos não executados. Nunca
  diminua o denominador, exclua código de produto ou escreva asserção vazia
  para atingi-la.
- **Ao remover um teste,** confira que outro ainda pega o defeito que ele
  protegia. Se nenhum pega e o risco é real, reescreva em vez de remover.

## Diante de vermelho

Decida de quem é o defeito antes de mexer. Teste que mede texto literal reprova
mudança legítima, e então quem se corrige é a asserção, e a nova fica mais
precisa, nunca mais permissiva. Se o teste estava certo, o código está errado,
mesmo que a mudança pareça inocente. Se parecer defeito do ambiente, prove
antes de descartar: rode o mesmo comando sobre o commit anterior. Nunca
escreva código de produção para um teste passar, nem desmarque a camada com
banco para a suíte ficar mais rápida.

## T12. O que a documentação não fixa

A contagem de testes, a lista de arquivos de uma camada (o marcador diz), "ainda
não tem teste" sem data, e teste obrigatório para toda mudança
independentemente do risco.
