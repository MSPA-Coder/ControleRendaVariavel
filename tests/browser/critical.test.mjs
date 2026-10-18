import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.BASE_URL ?? 'http://browser-web:5003';
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function logged(username, run) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  try {
    await page.goto(`${base}/login`);
    await page.getByLabel('Usuário').fill(username);
    await page.getByLabel('Senha').fill('Synthetic-browser-only-2026!');
    await Promise.all([page.waitForURL(`${base}/`), page.getByRole('button', { name: 'Entrar', exact: true }).click()]);
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
  assert.match(href, /^\/positions\/\d+$/);
  await page.goto(`${base}${href}/edit`);
}

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
