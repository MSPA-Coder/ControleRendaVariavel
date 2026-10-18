// Lê um relatório Excel do myProfit dentro da página já logada e devolve as linhas.
//
// Uso (no console da aba https://myprofitweb.com/..., com o titular logado):
//   const r = await lerRelatorio(0, 'Histórico de investimentos', '2026-10-01', '2026-10-31');
//   copy(linhasEmTsv(r.linhas));   // ou imprimir
//
// Relatórios usados na conciliação mensal:
//   0  Histórico de investimentos   (negócios do período)
//   11 Posição detalhada            (posição na data final)
//   1  Operações finalizadas        (resultado realizado no período)
//
// O arquivo vem como .xlsx (zip); aqui ele é lido com fetch + DecompressionStream,
// porque o botão do site usa window.open, que o navegador embutido bloqueia.
// As duas primeiras linhas trazem o nome e o CPF do titular: nunca as imprima nem as grave.
window.lerRelatorio = async function (relatorio, titulo, de, ate) {
  const url = '/API/Report?report=' + relatorio + '&reporttitle=' + encodeURIComponent(titulo) +
    '&exchange=&asset=&format=xls&from=' + de + '&to=' + ate + '&customerID=&mobile=0&market=';
  const resposta = await fetch(url, { credentials: 'include' });
  const buf = new Uint8Array(await resposta.arrayBuffer());
  if (buf[0] !== 0x50 || buf[1] !== 0x4b) {
    return { erro: 'resposta não é um arquivo .xlsx (sessão expirada?)', status: resposta.status };
  }
  const dv = new DataView(buf.buffer);
  let fim = buf.length - 22;
  while (dv.getUint32(fim, true) !== 0x06054b50) fim--;
  const total = dv.getUint16(fim + 10, true);
  let p = dv.getUint32(fim + 16, true);
  const entradas = {};
  for (let i = 0; i < total; i++) {
    const metodo = dv.getUint16(p + 10, true), tamanho = dv.getUint32(p + 20, true);
    const nl = dv.getUint16(p + 28, true), xl = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true);
    const desloc = dv.getUint32(p + 42, true);
    const nome = new TextDecoder().decode(buf.slice(p + 46, p + 46 + nl));
    entradas[nome] = { metodo, tamanho, desloc };
    p += 46 + nl + xl + cl;
  }
  const ler = async (nome) => {
    const e = entradas[nome];
    if (!e) return null;
    const lnl = dv.getUint16(e.desloc + 26, true), lxl = dv.getUint16(e.desloc + 28, true);
    const ini = e.desloc + 30 + lnl + lxl;
    const dados = buf.slice(ini, ini + e.tamanho);
    if (e.metodo === 0) return new TextDecoder().decode(dados);
    const fluxo = new Blob([dados]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return await new Response(fluxo).text();
  };
  const compartilhadas = await ler('xl/sharedStrings.xml');
  const planilha = await ler('xl/worksheets/sheet1.xml');
  const dom = new DOMParser();
  const textos = compartilhadas
    ? [...dom.parseFromString(compartilhadas, 'text/xml').getElementsByTagName('si')]
        .map((s) => [...s.getElementsByTagName('t')].map((t) => t.textContent).join(''))
    : [];
  const linhas = [];
  for (const linha of dom.parseFromString(planilha, 'text/xml').getElementsByTagName('row')) {
    const celulas = {};
    let max = 0;
    for (const c of linha.getElementsByTagName('c')) {
      const ref = c.getAttribute('r').replace(/\d+/g, '');
      let col = 0;
      for (const ch of ref) col = col * 26 + ch.charCodeAt(0) - 64;
      col--;
      const tipo = c.getAttribute('t');
      const v = c.getElementsByTagName('v')[0];
      const inline = c.getElementsByTagName('is')[0];
      let valor = '';
      if (tipo === 's' && v) valor = textos[+v.textContent];
      else if (inline) valor = inline.textContent;
      else if (v) valor = v.textContent;
      celulas[col] = valor;
      if (col > max) max = col;
    }
    const arr = [];
    for (let i = 0; i <= max; i++) arr.push(celulas[i] ?? '');
    linhas.push(arr);
  }
  return { linhas, status: resposta.status };
};

// TSV a partir das linhas: o cabeçalho anonimizado (nome e CPF viram um texto fixo).
window.linhasEmTsv = function (linhas) {
  const saida = ['Emitido por: TITULAR ANONIMIZADO - 000.000.000-00'];
  saida.push(linhas[1] && /^Per/.test(linhas[1][0]) ? linhas[1][0] : 'Período: ?');
  for (const l of linhas.slice(2)) saida.push(l.join('\t'));
  return saida.join('\n');
};
