// Coleta do myProfit para o fechamento: um pacote só, que o CB e o CRV importam.
//
// Uso (na aba https://myprofitweb.com/App/... já logada, depois de carregar
// ler-relatorio.js, que define lerRelatorio e linhasEmTsv):
//   const pacote = await coletarMyProfit('2026-10-01', '2026-10-20');
//   // opcional: datas de fim de mês desde outra data (abertura de conta nova)
//   const pacote = await coletarMyProfit('2026-10-01', '2026-10-20', { fimDeMesDesde: '2025-12-31' });
//
// Devolve o texto JSON do pacote `myprofit-coleta/1`:
//   posicoes     posição por corretora em cada fim de mês do intervalo e no dia `ate`
//                (/API/Stock?date=...), como tabela ({colunas, linhas}): é dela que o
//                CB tira Tesouro, renda fixa EUA e caixa em dólar, e o CRV, o retrato
//                da conciliação;
//   cambio       notas de câmbio (/API/ExchangeDoc);
//   proventos    proventos pagos no período (/API/Provents);
//   negocios_tsv o "Histórico de investimentos" do período, já anonimizado.
//
// As APIs do myProfit devolvem também o cadastro do titular (nome, CPF, e-mail,
// telefone). Aqui só se copiam os campos listados em cada `pegar`; e, por
// garantia, o pacote é recusado se algo com cara de CPF ou de e-mail sobrar nele.
window.coletarMyProfit = async function (de, ate, opcoes = {}) {
  if (typeof window.lerRelatorio !== 'function' || typeof window.linhasEmTsv !== 'function') {
    throw new Error('Carregue scripts/myprofit/ler-relatorio.js antes.');
  }
  const json = async (url) => {
    const resposta = await fetch(url, { credentials: 'include' });
    if (!resposta.ok) throw new Error(`${url} respondeu ${resposta.status} (sessão expirada?)`);
    return resposta.json();
  };
  const dia = (texto) => (texto ? String(texto).slice(0, 10) : null);
  const fimDoMes = (ano, mes) => new Date(Date.UTC(ano, mes + 1, 0)).toISOString().slice(0, 10);

  // Fins de mês desde `fimDeMesDesde` (padrão: o fim do mês anterior a `de`) até antes de `ate`, mais `ate`.
  const inicio = new Date(`${opcoes.fimDeMesDesde || de}T00:00:00Z`);
  const datas = [];
  let ano = inicio.getUTCFullYear(), mes = inicio.getUTCMonth() - (opcoes.fimDeMesDesde ? 0 : 1);
  for (;;) {
    if (mes < 0) { mes += 12; ano -= 1; }
    if (mes > 11) { mes -= 12; ano += 1; }
    const fim = fimDoMes(ano, mes);
    if (fim >= ate) break;
    datas.push(fim);
    mes += 1;
  }
  datas.push(ate);

  // Números com seis casas: o que passar disso é ruído do cálculo do myProfit.
  const n = (v) => (typeof v === 'number' ? Math.round(v * 1e6) / 1e6 : v);
  // Posições em forma de tabela (o cabeçalho uma vez só): o pacote fica com metade do tamanho.
  const colunas = ['ticker', 'corretora', 'grupo', 'classe', 'quantidade', 'preco_medio', 'custo_brl', 'valor_brl', 'valor_usd'];
  const posicoes = {};
  for (const data of datas) {
    const linhas = await json(`/API/Stock?date=${data}&checkB3=false`);
    posicoes[data] = {
      colunas,
      linhas: linhas.map((x) => {
        const grupo = x.GroupName || '';
        // O caixa em dólar só aparece na data de hoje: nas passadas o myProfit não o publica.
        const caixa = /conta corrente em d[oó]lar/i.test(grupo) || /^conta corrente em d[oó]lar/i.test(x.Ticker || '');
        const classe = caixa ? 'CASH_USD' : ((x.Asset && x.Asset.AssetGroup) || '');
        return [x.Ticker, x.ExchangeName, grupo, classe, n(x.Qty), n(x.PriceBuy), n(x.TotalBuy),
          n(x.TotalSellAdjusted), n(x.TotalSellAdjustedUSD)];
      }),
    };
  }

  const cambio = (await json('/API/ExchangeDoc')).map((x) => ({
    data: dia(x.DocumentDate), numero: x.Number, corretora: x.Exchange,
    para: x.To, valor_de: n(x.FromValue), valor_para: n(x.ToValue),
  }));

  const brutos = await json(`/API/Provents?period=99&dateFrom=${de}&dateTo=${ate}&filter=`);
  const proventos = (brutos.Provents || [])
    .map((x) => ({
      ticker: x.Ticker, tipo: x.Type, corretora: x.ExchangeName,
      valor: n(x.Total), valor_usd: n(x.TotalUSD), imposto: n(x.Tax), data_pagamento: dia(x.DatePay),
    }))
    .filter((p) => p.data_pagamento && p.data_pagamento >= de && p.data_pagamento <= ate);

  const historico = await window.lerRelatorio(0, 'Histórico de investimentos', de, ate);
  if (historico.erro) throw new Error(`Histórico de investimentos: ${historico.erro}`);
  const negocios_tsv = window.linhasEmTsv(historico.linhas);

  const pacote = JSON.stringify({
    formato: 'myprofit-coleta/1', coletado_em: new Date().toISOString(), de, ate,
    posicoes, cambio, proventos, negocios_tsv,
  });
  if (/\d{3}\.\d{3}\.\d{3}-\d{2}/.test(pacote.replace('000.000.000-00', '')) || /[\w.+-]+@[\w-]+\.[\w.]+/.test(pacote)) {
    throw new Error('O pacote ficou com algo parecido com CPF ou e-mail; nada foi devolvido.');
  }
  return pacote;
};
