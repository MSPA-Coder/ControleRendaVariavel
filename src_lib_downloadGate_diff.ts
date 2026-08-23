--- src/lib/downloadGate.ts (原始)


+++ src/lib/downloadGate.ts (修改后)
/**
 * A página pode estar dentro de um iframe (preview do workspace), que em geral
 * bloqueia downloads. Detectamos isso e abrimos um assistente que orienta o
 * usuário a abrir a página em uma aba completa do navegador — lá o .zip cai
 * na pasta de Downloads de verdade.
 */

export interface GatePayload {
  file: string;
  retry: () => void;
}

type GateListener = (p: GatePayload) => void;

let listener: GateListener | null = null;

export function setGateListener(l: GateListener | null) {
  listener = l;
}

export function isFramed(): boolean {
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

export function reportFramedDownload(file: string, retry: () => void) {
  if (isFramed()) listener?.({ file, retry });
}

export function openInTopTab() {
  window.open(window.location.href, "_blank", "noopener");
}
