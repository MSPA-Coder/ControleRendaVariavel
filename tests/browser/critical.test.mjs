import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.BASE_URL ?? 'http://browser-web:5003';
let browser;
const sessions = new Map();
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function logged(username, run) {
  // Uma autenticação real por conta; consultas da mesma conta reutilizam a sessão.
  // Evita que a suíte serial esgote o limite legítimo de login por IP.
  const context = await browser.newContext(sessions.has(username) ? { storageState: sessions.get(username) } : {});
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  try {
    if (sessions.has(username)) {
      await page.goto(`${base}/`);
    } else {
      await page.goto(`${base}/login`);
      await page.getByLabel('Usuário').fill(username);
      await page.getByLabel('Senha').fill('Synthetic-browser-only-2026!');
      await Promise.all([page.waitForURL(`${base}/`), page.getByRole('button', { name: 'Entrar', exact: true }).click()]);
      sessions.set(username, await context.storageState());
    }
    await run(page);
  } catch (error) {
    await page.screenshot({ path: `/results/${username}-${Date.now()}.png`, fullPage: true });
    throw error;
  } finally { await context.close(); }
}

async function fillPosition(page, ticker, region, quantity = '10.5', cost = '20.25') {
  await page.goto(`${base}/positions/new`);
  await page.locator('select[name="broker_id"]').selectOption({ label: 'Corretora browser' });
  const option = page.locator('select[name="ticker_id"] option').filter({ hasText: ticker });
  await page.locator('select[name="ticker_id"]').selectOption(await option.getAttribute('value'));
  await page.locator('select[name="portfolio_id"]').selectOption({ label: 'Carteira browser' });
  await page.getByLabel('Quantidade', { exact: true }).fill(region === 'us' ? quantity : quantity.replace('.', ','));
  await page.getByLabel('Custo médio', { exact: true }).fill(region === 'us' ? cost : cost.replace('.', ','));
  await page.getByLabel('Data inicial', { exact: true }).fill(region === 'us' ? '06/10/2026' : '10/06/2026');
  await page.getByLabel('Multiplicador do target', { exact: true }).fill(region === 'us' ? '1.5' : '1,5');
  await page.getByLabel('Multiplicador do target', { exact: true }).blur();
}

async function save(page) {
  await page.getByRole('button', { name: 'Salvar', exact: true }).click();
  await Promise.all([
    page.waitForURL(`${base}/`),
    page.getByRole('dialog').getByRole('button', { name: 'Confirmar', exact: true }).click(),
  ]);
}

async function editFor(page, ticker) {
  const href = await page.getByRole('link', { name: ticker, exact: true }).getAttribute('href');
  const path = new URL(href, base).pathname;
  assert.match(path, /^\/positions\/\d+$/);
  await page.goto(`${base}${path}/edit`);
}

test('consultas preservam origem, detalhes, filtros e privacidade', { timeout: 45000 }, async () => {
  await logged('browser-demo', async page => {
    await page.goto(`${base}/dividends`);
    await page.getByRole('button', { name: 'Mostrar os proventos de PETR4', exact: true }).click();
    const receipts = page.getByRole('region', { name: 'Recebimentos de PETR4', exact: true });
    await receipts.waitFor();
    assert.equal(await receipts.getByText('Controle Bancário · somente leitura', { exact: false }).count(), 2);
    assert.equal(await receipts.getByRole('link', { name: 'Editar provento', exact: true }).count(), 0);
    await receipts.locator('summary').first().click();
    const values = receipts.locator('[data-sensitive-value="true"]');
    assert.ok(await values.count() > 4);
    await page.getByRole('button', { name: 'Abrir filtros globais' }).click();
    await page.getByRole('button', { name: 'Ativar modo discreto', exact: false }).click();
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: 'Mostrar os proventos de PETR4', exact: true }).click();
    await page.getByRole('region', { name: 'Recebimentos de PETR4', exact: true }).waitFor();
    await page.getByRole('region', { name: 'Recebimentos de PETR4', exact: true }).locator('summary').first().click();
    const hidden = await page.getByRole('region', { name: 'Recebimentos de PETR4', exact: true })
      .locator('[data-sensitive-value="true"]').evaluateAll(nodes => nodes.every(node =>
        getComputedStyle(node).color === 'rgba(0, 0, 0, 0)' || getComputedStyle(node).filter.includes('blur') ||
        getComputedStyle(node).fontSize === '0px'));
    assert.ok(hidden, 'Valores detalhados de recebimentos também devem ser ocultados');
    await page.getByRole('button', { name: 'Abrir filtros globais' }).click();
    await page.getByRole('button', { name: 'Desativar modo discreto', exact: false }).click();
    await page.waitForLoadState('networkidle');
    await page.locator('select[name="broker"]').selectOption({ label: 'XP · demonstração' });
    await page.getByRole('heading', { name: 'Nenhum provento neste filtro' }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('broker'), 'XP · demonstração');
    await page.goto(`${base}/transactions?status=closed`);
    const transactionDetail = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: 'Detalhes · PETR4' }) });
    await transactionDetail.locator('summary').click();
    await transactionDetail.getByText('Operação histórica fictícia, independente das posições abertas da demonstração.', { exact: true }).waitFor();
    assert.equal(await page.locator('a[href$="/close"]').count(), 0);
    await page.goto(`${base}/executions/reconciliation`);
    await page.getByRole('status').filter({ hasText: 'CB não configurado.' }).waitFor();
    assert.equal(await page.getByRole('region', { name: 'Confere', exact: true }).count(), 0);
    await page.locator('summary').filter({ hasText: 'Contexto e documentos' }).first().click();
    await page.getByText('NOTA-DEMO-1', { exact: true }).waitFor();
  });
});

// Os pregões da demonstração nascem relativos a hoje (`tests/browser_server.py`):
// NOTA-DEMO-1 há 10 dias e NOTA-DEMO-2 há 9. Data fixa aqui vence sozinha.
// "Hoje" é o dia civil de São Paulo, como no servidor (`date.today()` com
// TZ=America/Sao_Paulo): pelo dia UTC, o teste erra das 21h à meia-noite.
function diasAtras(dias) {
  const iso = new Date(Date.now() - dias * 86400000)
    .toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
  const [ano, mes, dia] = iso.split('-');
  return { iso, br: `${dia}/${mes}/${ano}` };
}

test('colar data na conferência aplica o recorte dos pregões', { timeout: 45000 }, async () => {
  await logged('browser-demo', async page => {
    await page.goto(`${base}/executions/reconciliation?currency=BRL`);
    await page.getByText('NOTA-DEMO-1', { exact: true }).waitFor({ state: 'attached' });
    const start = page.locator('input[data-regional-view="date"]').first();
    await start.click();
    await page.keyboard.insertText(diasAtras(9).br);
    await start.press('Tab');
    await page.waitForURL(url => new URL(url).searchParams.get('start') === diasAtras(9).iso);
    await page.getByText('NOTA-DEMO-2', { exact: true }).waitFor({ state: 'attached' });
    assert.equal(await page.getByText('NOTA-DEMO-1', { exact: true }).count(), 0);
  });
});

test('requisição lenta de data não substitui filtro digitado depois', { timeout: 45000 }, async () => {
  await logged('browser-demo', async page => {
    await page.goto(`${base}/executions/reconciliation?currency=BRL`);
    const start = page.locator('input[data-regional-view="date"]').first();
    await page.route('**/executions/reconciliation?*', async route => {
      if (new URL(route.request().url()).searchParams.get('start') === diasAtras(10).iso) {
        await new Promise(resolve => setTimeout(resolve, 800));
      }
      await route.continue();
    });
    const firstRequest = page.waitForRequest(request =>
      new URL(request.url()).searchParams.get('start') === diasAtras(10).iso);
    await start.fill(diasAtras(10).br);
    await start.press('Tab');
    await firstRequest;
    await start.fill(diasAtras(9).br);
    await start.press('Tab');
    await page.waitForURL(url => new URL(url).searchParams.get('start') === diasAtras(9).iso);
    await page.getByText('NOTA-DEMO-2', { exact: true }).waitFor({ state: 'attached' });
    assert.equal(await page.getByText('NOTA-DEMO-1', { exact: true }).count(), 0);
  });
});

test('moeda global acompanha a conferência de pregões', { timeout: 45000 }, async () => {
  await logged('browser-demo', async page => {
    await page.goto(`${base}/executions/reconciliation?currency=USD`);
    await page.getByRole('status').filter({ hasText: 'CB não configurado.' }).waitFor();
    assert.equal(await page.getByText('NOTA-DEMO-1', { exact: true }).count(), 0);
    assert.equal(await page.locator('form.filters input[name="currency"]').inputValue(), 'USD');
    await page.goto(`${base}/executions/reconciliation?currency=ALL`);
    await page.getByText('NOTA-DEMO-1', { exact: true }).waitFor({ state: 'attached' });
  });
});

test('cancelar provento inválido preserva a moeda da lista de origem', { timeout: 45000 }, async () => {
  await logged('browser-demo', async page => {
    const origem = '/dividends?currency=USD';
    await page.goto(`${base}/dividends/new?volta=${encodeURIComponent(origem)}`);
    await page.locator('select[name="broker_id"]').selectOption({ label: 'Corretora browser' });
    const ticker = page.locator('select[name="ticker_id"] option').filter({ hasText: /^PETR4 ·/ });
    await page.locator('select[name="ticker_id"]').selectOption(await ticker.getAttribute('value'));
    await page.locator('input[data-regional-view="number"]').first().fill('1');
    await page.locator('input[data-regional-view="date"]').first().fill('01/12/2026');
    await page.getByRole('button', { name: 'Salvar', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'data de pagamento' }).waitFor();
    assert.equal(await page.locator('input[name="volta"]').inputValue(), origem);
    await page.getByRole('link', { name: 'Cancelar', exact: true }).click();
    assert.equal(new URL(page.url()).pathname, '/dividends');
    assert.equal(new URL(page.url()).searchParams.get('currency'), 'USD');
  });
});

test('cancelar abertura anterior mantém a moeda da lista de origem', { timeout: 45000 }, async () => {
  await logged('browser-flow', async page => {
    const origem = '/?currency=USD';
    await page.goto(`${base}/positions/new?volta=${encodeURIComponent(origem)}`);
    await page.locator('select[name="broker_id"]').selectOption({ label: 'Corretora browser' });
    const ticker = page.locator('select[name="ticker_id"] option').filter({ hasText: /^FLOW3 ·/ });
    await page.locator('select[name="ticker_id"]').selectOption(await ticker.getAttribute('value'));
    await page.locator('select[name="portfolio_id"]').selectOption({ label: 'Carteira browser' });
    await page.getByLabel('Quantidade', { exact: true }).fill('10');
    await page.getByLabel('Custo médio', { exact: true }).fill('10');
    await page.getByLabel('Data inicial', { exact: true }).fill('09/06/2026');
    await page.getByLabel('Multiplicador do target', { exact: true }).fill('1,5');
    await page.getByLabel('Multiplicador do target', { exact: true }).blur();
    await page.getByRole('button', { name: 'Salvar', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Data anterior à abertura registrada' }).waitFor();
    assert.equal(await page.locator('input[name="volta"]').inputValue(), origem);
    await page.getByRole('link', { name: 'Cancelar', exact: true }).click();
    assert.equal(new URL(page.url()).pathname, '/');
    assert.equal(new URL(page.url()).searchParams.get('currency'), 'USD');
  });
});

test('análise, mercado e cadastros permitem consulta e aprofundamento', { timeout: 45000 }, async () => {
  await logged('browser-demo', async page => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const path of ['/performance?period=all', '/risk', '/analysis/exposure-asset',
      '/analysis/exposure-broker', '/analysis/exposure-market', '/quotes', '/data-status',
      '/tables/portfolios', '/preferences', '/minha-senha']) {
      const response = await page.goto(`${base}${path}`);
      assert.equal(response.status(), 200, path);
      assert.equal(await page.getByRole('heading', { level: 1 }).count(), 1, path);
      if (path === '/quotes') {
        await Promise.all([
          page.waitForResponse(response => response.url().includes('/quotes?ticker_id=')),
          page.locator('#quote-ticker').selectOption({ label: 'PETR4' }),
        ]);
      }
      if (path.startsWith('/performance') || path === '/quotes' || path.startsWith('/analysis/')) {
        await page.locator('canvas').first().waitFor();
      }
    }
    assert.deepEqual(errors, []);
  });
});

test('admin consulta configurações e usuários; operador continua sem acesso', { timeout: 45000 }, async () => {
  const restricted = ['/settings', '/users', '/tables/brokers', '/tables/tickers',
    '/tables/options/expirations', '/tables/options/contracts'];
  await logged('browser-demo', async page => {
    for (const path of restricted) {
      const response = await page.request.get(`${base}${path}`);
      assert.equal(response.status(), 403, path);
    }
  });
  await logged('browser-admin', async page => {
    for (const path of restricted) {
      const response = await page.goto(`${base}${path}`);
      assert.equal(response.status(), 200, path);
      assert.equal(await page.getByRole('heading', { level: 1 }).count(), 1, path);
    }
    await page.goto(`${base}/tables/brokers`);
    await page.locator('summary').filter({ hasText: 'Adicionar corretora' }).click();
    await page.getByLabel('Nome', { exact: true }).waitFor();
  });
});

for (const region of ['br', 'us']) {
  test(`decimal ${region} chega ao servidor com valor correto`, { timeout: 45000 }, async () => {
    await logged(`browser-${region}`, async page => {
      await fillPosition(page, 'BROWSE3', region);
      await save(page);
      await editFor(page, 'BROWSE3');
      assert.equal(Number(await page.locator('[name="quantity"]').inputValue()), 10.5);
      assert.equal(Number(await page.locator('[name="average_cost"]').inputValue()), 20.25);
    });
  });
}

test('cancelar mantém o formulário; confirmar e reenviar grava uma vez', { timeout: 45000 }, async () => {
  await logged('browser-flow', async page => {
    await fillPosition(page, 'RETRY3', 'br', '100', '10');
    await page.getByRole('button', { name: 'Salvar', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancelar', exact: true }).click();
    assert.equal(new URL(page.url()).pathname, '/positions/new');
    const fields = await page.locator('form.position-form').evaluate(form => Object.fromEntries(new FormData(form)));
    await save(page);
    const replay = await page.request.post(`${base}/positions`, { form: fields });
    assert.equal(replay.status(), 200);
    await page.goto(`${base}/`);
    await editFor(page, 'RETRY3');
    assert.equal(Number(await page.locator('[name="quantity"]').inputValue()), 100);
  });
});

test('cancelar e confirmar encerramento parcial mantém o saldo correto', { timeout: 45000 }, async () => {
  await logged('browser-flow', async page => {
    await page.goto(`${base}/transactions?status=open`);
    const row = page.getByRole('row').filter({ hasText: 'FLOW3' });
    const href = await row.locator('a[href$="/close"]').getAttribute('href');
    assert.ok(href);
    await page.goto(`${base}${href}`);
    await page.getByRole('link', { name: 'Cancelar', exact: true }).click();
    await editFor(page, 'FLOW3');
    assert.equal(Number(await page.locator('[name="quantity"]').inputValue()), 20);
    await page.goto(`${base}${href}`);
    await page.getByLabel('Quantidade a encerrar', { exact: true }).fill('5');
    await page.getByLabel('Preço de saída', { exact: true }).fill('12,50');
    await page.getByRole('button', { name: 'Encerrar posição', exact: true }).click();
    await page.waitForURL(`${base}/transactions`);
    await page.goto(`${base}/`);
    await editFor(page, 'FLOW3');
    assert.equal(Number(await page.locator('[name="quantity"]').inputValue()), 15);
    assert.equal(Number(await page.locator('[name="average_cost"]').inputValue()), 10);
  });
});

async function quantidadeEmCarteira(page, ticker) {
  await page.goto(`${base}/`);
  await editFor(page, ticker);
  return {
    quantidade: Number(await page.locator('[name="quantity"]').inputValue()),
    custo: Number(await page.locator('[name="average_cost"]').inputValue()),
  };
}

test('desfazer o encerramento total devolve a posição à Carteira e a transação a aberta', { timeout: 60000 }, async () => {
  await logged('browser-flow', async page => {
    const antes = await quantidadeEmCarteira(page, 'UNDO3');
    assert.deepEqual(antes, { quantidade: 30, custo: 10 });
    // Encerra por inteiro pela tela; a data de saída é a do formulário (hoje, dia de São Paulo).
    await page.goto(`${base}/transactions?status=open`);
    const href = await page.getByRole('row').filter({ hasText: 'UNDO3' }).locator('a[href$="/close"]').getAttribute('href');
    await page.goto(`${base}${href}`);
    await page.getByLabel('Quantidade a encerrar', { exact: true }).fill('30');
    await page.getByLabel('Preço de saída', { exact: true }).fill('12,50');
    await page.getByRole('button', { name: 'Encerrar posição', exact: true }).click();
    await page.waitForURL(`${base}/transactions`);
    await page.goto(`${base}/`);
    assert.equal(await page.getByRole('link', { name: 'UNDO3', exact: true }).count(), 0);
    // Em Transações: "desfazer", confirmar no diálogo.
    await page.goto(`${base}/transactions?status=closed`);
    const linha = page.getByRole('row').filter({ hasText: 'UNDO3' });
    await linha.getByRole('button', { name: 'desfazer', exact: true }).click();
    const dialogo = page.getByRole('dialog');
    await dialogo.getByText('A posição e o extrato voltam para a Carteira', { exact: false }).waitFor();
    await dialogo.getByRole('button', { name: 'Confirmar', exact: true }).click();
    await linha.waitFor({ state: 'detached' });
    // A posição voltou com a mesma quantidade e o mesmo custo médio...
    assert.deepEqual(await quantidadeEmCarteira(page, 'UNDO3'), antes);
    // ...e a transação voltou a aberta (com o link "encerrar"), sem linha fechada.
    await page.goto(`${base}/transactions?status=open`);
    await page.getByRole('row').filter({ hasText: 'UNDO3' }).locator('a[href$="/close"]').waitFor();
    await page.goto(`${base}/transactions?status=closed`);
    assert.equal(await page.getByRole('row').filter({ hasText: 'UNDO3' }).count(), 0);
  });
});

test('encerramento total sem extrato guardado só se exclui com a confirmação do aviso', { timeout: 60000 }, async () => {
  await logged('browser-flow', async page => {
    await page.goto(`${base}/transactions?status=closed`);
    const linha = page.getByRole('row').filter({ hasText: 'SEMEX3' })
      .filter({ has: page.getByRole('button', { name: 'excluir', exact: true }) });
    await linha.getByRole('button', { name: 'excluir', exact: true }).click();
    const dialogo = page.getByRole('dialog');
    await dialogo.getByText('a posição NÃO será reaberta', { exact: false }).waitFor();
    await dialogo.getByRole('button', { name: 'Cancelar', exact: true }).click();
    assert.equal(await linha.count(), 1);
    await linha.getByRole('button', { name: 'excluir', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar', exact: true }).click();
    await linha.waitFor({ state: 'detached' });
    // Não reaberta: segue sem posição em Carteira.
    await page.goto(`${base}/`);
    assert.equal(await page.getByRole('link', { name: 'SEMEX3', exact: true }).count(), 0);
  });
});

test('modo discreto preserva filtro HTMX e mascara o detalhe do trade', { timeout: 45000 }, async () => {
  await logged('browser-demo', async page => {
    await page.getByLabel('Agrupar por corretora', { exact: true }).check();
    await page.waitForURL(url => url.searchParams.get('group_by_broker') === '1');
    await page.getByRole('button', { name: 'Abrir filtros globais', exact: true }).click();
    await Promise.all([
      page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/privacy')),
      page.locator('[data-privacy-submit]').click(),
    ]);
    await page.waitForLoadState('networkidle');
    assert.equal(new URL(page.url()).searchParams.get('group_by_broker'), '1');
    await page.getByRole('link', { name: 'PETR4', exact: true }).click();
    await page.getByRole('heading', { name: 'PETR4', exact: true }).waitFor();
    assert.doesNotMatch(await page.locator('main').innerText(), /R\$\s*36,42/);
    await page.getByRole('link', { name: '← Voltar às posições de ações', exact: true }).click();
    assert.equal(new URL(page.url()).searchParams.get('group_by_broker'), '1');
    assert.equal(await page.getByLabel('Agrupar por corretora', { exact: true }).isChecked(), true);
  });
});
