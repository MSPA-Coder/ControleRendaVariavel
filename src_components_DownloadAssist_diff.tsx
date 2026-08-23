--- src/components/DownloadAssist.tsx (原始)


+++ src/components/DownloadAssist.tsx (修改后)
import { useEffect } from "react";
import { openInTopTab, type GatePayload } from "../lib/downloadGate";
import { useCopy } from "../hooks/useCopy";
import { ArrowUpRightIcon, CheckIcon, CopyIcon, DownloadIcon } from "./icons";

const DEST = "C:\\Users\\MSPA\\Downloads";

const STEPS = [
  "Abrir esta página em uma aba do navegador",
  "Na aba aberta, clicar no botão do .zip outra vez",
  "O arquivo aparece na sua pasta Downloads",
];

export function DownloadAssist({
  payload,
  onClose,
}: {
  payload: GatePayload | null;
  onClose: () => void;
}) {
  const [copied, copy] = useCopy();

  useEffect(() => {
    if (!payload) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [payload, onClose]);

  if (!payload) return null;
  const fullPath = `${DEST}\\${payload.file}`;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label="Assistente de download"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      style={{ background: "color-mix(in oklab, #04070d 58%, transparent)", backdropFilter: "blur(3px)" }}
    >
      <div
        className="toast-in w-full max-w-lg overflow-hidden rounded-[var(--radius-lg)] border shadow-[0_36px_90px_-24px_rgba(0,0,0,0.65)]"
        style={{ background: "var(--card)", borderColor: "var(--border)" }}
      >
        {/* cabeçalho */}
        <div
          className="flex items-start gap-3 border-b px-5 py-4"
          style={{ borderColor: "var(--border)", background: "var(--bg-header)" }}
        >
          <span
            className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--radius)]"
            style={{ background: "var(--primary-light)", color: "var(--primary)" }}
          >
            <DownloadIcon className="text-[1.25rem]" />
            <span
              className="live-dot absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2"
              style={{ background: "var(--warning)", borderColor: "var(--bg-header)" }}
            />
          </span>
          <div className="min-w-0">
            <h2 className="font-display text-[1.02rem] font-bold leading-tight" style={{ color: "var(--text)" }}>
              Não achou o arquivo em Downloads?
            </h2>
            <p className="mt-0.5 text-[0.76rem] leading-snug" style={{ color: "var(--muted)" }}>
              O site está rodando dentro do <strong style={{ color: "var(--text)" }}>preview do workspace</strong> — e
              previews costumam bloquear (ou guardar em outro lugar) os downloads.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto shrink-0 rounded-md border px-2 py-0.5 font-mono text-[0.72rem] transition-colors"
            style={{ borderColor: "var(--border)", color: "var(--muted)", background: "var(--card)" }}
            aria-label="Fechar assistente"
          >
            ✕
          </button>
        </div>

        {/* corpo */}
        <div className="px-5 py-4">
          <p className="text-[0.78rem] font-medium" style={{ color: "var(--text)" }}>
            O caminho garantido é em 3 cliques:
          </p>
          <ol className="mt-2.5 flex flex-col gap-2">
            {STEPS.map((s, i) => (
              <li key={s} className="flex items-center gap-2.5">
                <span
                  className="flex h-5.5 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.68rem] font-bold"
                  style={{ background: "var(--primary)", color: "var(--bg-page)" }}
                >
                  {i + 1}
                </span>
                {i === 0 ? (
                  <button
                    type="button"
                    onClick={openInTopTab}
                    className="group inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-3 py-1.5 font-mono text-[0.72rem] font-bold transition-all duration-200 hover:-translate-y-px"
                    style={{
                      background: "var(--primary)",
                      borderColor: "var(--primary)",
                      color: "var(--bg-page)",
                      boxShadow: "0 8px 20px -10px color-mix(in oklab, var(--primary) 75%, transparent)",
                    }}
                  >
                    Abrir em nova aba
                    <ArrowUpRightIcon className="text-[0.72rem] transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                  </button>
                ) : (
                  <span className="text-[0.78rem]" style={{ color: "var(--muted)" }}>
                    {s}
                  </span>
                )}
              </li>
            ))}
          </ol>

          {/* destino */}
          <div
            className="mt-3.5 flex items-center gap-2 overflow-x-auto rounded-[var(--radius)] border px-3 py-2"
            style={{ background: "color-mix(in oklab, var(--text) 5%, var(--bg-page))", borderColor: "var(--border)" }}
          >
            <span className="shrink-0 font-mono text-[0.62rem] font-bold uppercase tracking-wider" style={{ color: "var(--muted)" }}>
              destino
            </span>
            <code className="whitespace-nowrap font-mono text-[0.72rem] font-semibold" style={{ color: "var(--text)" }}>
              {fullPath}
            </code>
            <button
              type="button"
              onClick={() => copy(fullPath)}
              className="ml-auto shrink-0 rounded p-1 transition-all duration-150 hover:-translate-y-px"
              style={{ color: copied ? "var(--success)" : "var(--muted)" }}
              aria-label={copied ? "Caminho copiado" : "Copiar caminho de destino"}
            >
              {copied ? <CheckIcon /> : <CopyIcon />}
            </button>
          </div>
        </div>

        {/* rodapé */}
        <div
          className="flex flex-wrap items-center gap-2 border-t px-5 py-3"
          style={{ borderColor: "var(--border)", background: "var(--bg-header)" }}
        >
          <button
            type="button"
            onClick={openInTopTab}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-3 py-1.5 font-mono text-[0.7rem] font-bold transition-all duration-200 hover:-translate-y-px"
            style={{ background: "var(--primary)", borderColor: "var(--primary)", color: "var(--bg-page)" }}
          >
            <ArrowUpRightIcon className="text-[0.72rem]" />
            abrir em nova aba
          </button>
          <button
            type="button"
            onClick={() => payload.retry()}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-3 py-1.5 font-mono text-[0.7rem] font-semibold transition-all duration-150 hover:-translate-y-px"
            style={{ borderColor: "var(--border)", background: "var(--card)", color: "var(--text)" }}
          >
            <DownloadIcon className="text-[0.78rem]" />
            tentar baixar aqui
          </button>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto font-mono text-[0.68rem] transition-colors hover:opacity-70"
            style={{ color: "var(--muted)" }}
          >
            entendi, fechar
          </button>
        </div>
      </div>
    </div>
  );
}
