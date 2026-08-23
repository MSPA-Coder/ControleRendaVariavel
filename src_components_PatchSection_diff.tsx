--- src/components/PatchSection.tsx (原始)
import { useEffect, useMemo, useRef, useState } from "react";
import { buildPatchFiles, WIN_TARGET, type PatchFile } from "../lib/patch";
import { downloadPatchZip, downloadWindowsPatchZip, saveText } from "../lib/download";
import { useCopy } from "../hooks/useCopy";
import {
  ArrowUpRightIcon,
  BrowserIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  GitHubIcon,
  TerminalIcon,
} from "./icons";
import { Reveal } from "./Reveal";

const KIND_STYLE: Record<PatchFile["kind"], { label: string; color: string }> = {
  novo: { label: "NOVO", color: "var(--success)" },
  edicao: { label: "EDIÇÃO", color: "var(--warning)" },
  comando: { label: "TERMINAL", color: "var(--primary)" },
};

/* ------------------------------------------------------------------ */
/* Quick start — modo terminal (3 comandos)                            */
/* ------------------------------------------------------------------ */

const QUICK_STEPS: { n: string; desc: string; cmd: string }[] = [
  {
    n: "1",
    desc: "Clone (ou entre na pasta que já existe)",
    cmd: "git clone https://github.com/MSPA-Coder/ControleRendaVariavel.git && cd ControleRendaVariavel",
  },
  {
    n: "2",
    desc: "Extraia o .zip na raiz do repositório (os caminhos já estão prontos)",
    cmd: "unzip -o temas-crv-patch.zip",
  },
  {
    n: "3",
    desc: "Rode o instalador e confirme o push no final",
    cmd: "chmod +x aplicar_patch.sh && ./aplicar_patch.sh",
  },
];

function QuickStep({ step }: { step: (typeof QUICK_STEPS)[number] }) {
  const [copied, copy] = useCopy();
  return (
    <div className="flex min-w-0 items-start gap-3">
      <span
        className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.72rem] font-bold"
        style={{ background: "var(--primary)", color: "var(--bg-page)" }}
      >
        {step.n}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[0.72rem] font-medium" style={{ color: "var(--muted)" }}>
          {step.desc}
        </p>
        <div
          className="group mt-1 flex items-center gap-2 overflow-x-auto rounded-[var(--radius-sm)] border px-2.5 py-1.5"
          style={{ background: "color-mix(in oklab, var(--text) 5%, var(--bg-page))", borderColor: "var(--border)" }}
        >
          <code className="whitespace-nowrap font-mono text-[0.7rem]" style={{ color: "var(--text)" }}>
            <span style={{ color: "var(--primary)" }}>$ </span>
            {step.cmd}
          </code>
          <button
            type="button"
            onClick={() => copy(step.cmd)}
            className="ml-auto shrink-0 rounded p-1 transition-all duration-150 hover:-translate-y-px"
            style={{ color: copied ? "var(--success)" : "var(--muted)" }}
            title={copied ? "Copiado!" : "Copiar comando"}
            aria-label={copied ? "Copiado" : `Copiar passo ${step.n}`}
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Modo navegador — github.dev + checklist interativo                  */
/* ------------------------------------------------------------------ */

const CHECKLIST_KEY = "crv-patch-checklist";
const ALEMBIC_CMD = 'alembic revision --autogenerate -m "add users.ui_theme" && alembic upgrade head';

function CheckRow({
  file,
  checked,
  onToggle,
  onOpen,
}: {
  file: PatchFile;
  checked: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  const [copied, copy] = useCopy();
  const ks = KIND_STYLE[file.kind];
  return (
    <li
      className="flex items-center gap-2.5 rounded-[var(--radius-sm)] border px-2.5 py-1.5 transition-colors duration-150"
      style={{
        borderColor: checked ? "color-mix(in oklab, var(--success) 45%, var(--border))" : "var(--border)",
        background: checked ? "color-mix(in oklab, var(--success) 6%, var(--card))" : "var(--card)",
        opacity: checked ? 0.8 : 1,
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-label={checked ? `Desmarcar ${file.path}` : `Marcar ${file.path} como colado`}
        className="flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded border transition-all duration-150"
        style={{
          width: "1.15rem",
          height: "1.15rem",
          borderColor: checked ? "var(--success)" : "var(--border)",
          background: checked ? "var(--success)" : "transparent",
          color: "var(--bg-page)",
        }}
      >
        {checked && <CheckIcon className="text-[0.66rem]" />}
      </button>
      <code
        className="min-w-0 flex-1 truncate font-mono text-[0.7rem] font-semibold"
        style={{ color: checked ? "var(--muted)" : "var(--text)", textDecoration: checked ? "line-through" : "none" }}
      >
        {file.path}
      </code>
      <span
        className="hidden rounded px-1.5 py-px font-mono text-[0.55rem] font-bold tracking-wider sm:inline-block"
        style={{ color: ks.color, background: `color-mix(in oklab, ${ks.color} 13%, transparent)` }}
      >
        {ks.label}
      </span>
      <button
        type="button"
        onClick={() => {
          copy(file.content);
          onOpen();
        }}
        className="inline-flex shrink-0 items-center gap-1 rounded-[var(--radius-sm)] border px-2 py-0.5 font-mono text-[0.62rem] font-semibold transition-all duration-150 hover:-translate-y-px"
        style={{
          borderColor: "var(--border)",
          background: copied ? "color-mix(in oklab, var(--success) 10%, var(--card))" : "var(--bg-page)",
          color: copied ? "var(--success)" : "var(--primary)",
        }}
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
        {copied ? "copiado" : "copiar"}
      </button>
    </li>
  );
}

function BrowserMode({
  files,
  onOpen,
}: {
  files: PatchFile[];
  onOpen: (index: number) => void;
}) {
  const checklist = useMemo(
    () => files.filter((f) => !["aplicar_patch.sh", "aplicar_patch.bat", "editar.py"].includes(f.path)),
    [files],
  );
  const [done, setDone] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem(CHECKLIST_KEY) || "{}");
    } catch {
      return {};
    }
  });
  const [cmdCopied, copyCmd] = useCopy();

  useEffect(() => {
    try {
      localStorage.setItem(CHECKLIST_KEY, JSON.stringify(done));
    } catch {
      /* armazenamento indisponível */
    }
  }, [done]);

  const doneCount = checklist.filter((f) => done[f.path]).length;
  const progress = checklist.length ? doneCount / checklist.length : 0;

  return (
    <div className="flex min-w-0 flex-col gap-3.5">
      {/* passo 1 */}
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.72rem] font-bold"
          style={{ background: "var(--primary)", color: "var(--bg-page)" }}
        >
          1
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[0.72rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            Abra o repositório <strong style={{ color: "var(--text)" }}>logado na sua conta</strong> e use os botões do
            próprio GitHub — sem download, sem terminal, sem editor. Cada{" "}
            <strong style={{ color: "var(--text)" }}>Commit changes</strong> publica o arquivo na hora:
          </p>
          <a
            href="https://github.com/MSPA-Coder/ControleRendaVariavel"
            target="_blank"
            rel="noreferrer"
            className="group mt-1.5 inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.68rem] font-semibold transition-all duration-150 hover:-translate-y-px"
            style={{ borderColor: "var(--primary)", color: "var(--primary)", background: "var(--primary-light)" }}
          >
            <GitHubIcon className="text-[0.85rem]" />
            github.com/MSPA-Coder/ControleRendaVariavel
            <ArrowUpRightIcon className="text-[0.62rem] transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
          </a>
          <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
            <div
              className="rounded-[var(--radius-sm)] border p-2.5"
              style={{ borderColor: "var(--border)", background: "color-mix(in oklab, var(--text) 3%, var(--card))" }}
            >
              <span
                className="rounded px-1.5 py-px font-mono text-[0.56rem] font-bold tracking-wider"
                style={{ color: "var(--success)", background: "color-mix(in oklab, var(--success) 13%, transparent)" }}
              >
                4× NOVO
              </span>
              <p className="mt-1.5 text-[0.68rem] leading-relaxed" style={{ color: "var(--muted)" }}>
                <strong style={{ color: "var(--text)" }}>Add file</strong> ▸{" "}
                <strong style={{ color: "var(--text)" }}>Create new file</strong> ▸ digite o caminho (ex.:{" "}
                <code className="font-mono text-[0.64rem]" style={{ color: "var(--primary)" }}>
                  app/themes.py
                </code>
                ) ▸ cole o conteúdo ▸ <strong style={{ color: "var(--text)" }}>Commit changes</strong>
              </p>
            </div>
            <div
              className="rounded-[var(--radius-sm)] border p-2.5"
              style={{ borderColor: "var(--border)", background: "color-mix(in oklab, var(--text) 3%, var(--card))" }}
            >
              <span
                className="rounded px-1.5 py-px font-mono text-[0.56rem] font-bold tracking-wider"
                style={{ color: "var(--warning)", background: "color-mix(in oklab, var(--warning) 13%, transparent)" }}
              >
                3× EDIÇÃO
              </span>
              <p className="mt-1.5 text-[0.68rem] leading-relaxed" style={{ color: "var(--muted)" }}>
                abra o arquivo (ex.:{" "}
                <code className="font-mono text-[0.64rem]" style={{ color: "var(--primary)" }}>
                  app/models.py
                </code>
                ) ▸ ícone do <strong style={{ color: "var(--text)" }}>lápis</strong> ▸ acrescente as linhas com{" "}
                <code className="font-mono text-[0.64rem]" style={{ color: "var(--success)" }}>+</code> ▸{" "}
                <strong style={{ color: "var(--text)" }}>Commit changes</strong>
              </p>
            </div>
          </div>
          <p className="mt-2 text-[0.64rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            Prefere um editor de verdade? Pressione a tecla{" "}
            <kbd
              className="rounded border px-1.5 py-px font-mono text-[0.62rem] font-bold"
              style={{ borderColor: "var(--border)", background: "var(--bg-page)", color: "var(--text)" }}
            >
              .
            </kbd>{" "}
            na página do repositório e edite tudo no{" "}
            <a
              href="https://github.dev/MSPA-Coder/ControleRendaVariavel"
              target="_blank"
              rel="noreferrer"
              className="font-semibold underline decoration-dotted underline-offset-2 transition-opacity hover:opacity-70"
              style={{ color: "var(--primary)" }}
            >
              github.dev
            </a>{" "}
            com um commit só no final.
          </p>
        </div>
      </div>

      {/* passo 2 — checklist */}
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.72rem] font-bold"
          style={{ background: "var(--primary)", color: "var(--bg-page)" }}
        >
          2
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <p className="text-[0.72rem] font-medium" style={{ color: "var(--muted)" }}>
              Em cada arquivo: <strong style={{ color: "var(--text)" }}>copie aqui → crie ou edite no GitHub (passo
              1)</strong>. Marque conforme for commitando:
            </p>
            <span
              className="rounded-full border px-2 py-px font-mono text-[0.62rem] font-bold"
              style={{
                borderColor: progress === 1 ? "var(--success)" : "var(--border)",
                color: progress === 1 ? "var(--success)" : "var(--primary)",
              }}
            >
              {doneCount}/{checklist.length} colados
            </span>
          </div>
          <div
            className="mt-1.5 h-1 overflow-hidden rounded-full"
            style={{ background: "color-mix(in oklab, var(--border) 70%, transparent)" }}
          >
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${progress * 100}%`, background: progress === 1 ? "var(--success)" : "var(--primary)" }}
            />
          </div>
          <ul className="mt-2 flex flex-col gap-1.5">
            {checklist.map((f) => (
              <CheckRow
                key={f.path}
                file={f}
                checked={!!done[f.path]}
                onToggle={() => setDone((d) => ({ ...d, [f.path]: !d[f.path] }))}
                onOpen={() => onOpen(files.findIndex((x) => x.path === f.path))}
              />
            ))}
          </ul>
          <p className="mt-1.5 text-[0.64rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            Arquivos <strong style={{ color: "var(--warning)" }}>EDIÇÃO</strong> não substituem o arquivo: procure o
            trecho indicado e acrescente o que está com{" "}
            <code className="font-mono" style={{ color: "var(--success)" }}>+</code> no início da linha.
          </p>
        </div>
      </div>

      {/* passo 3 */}
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.72rem] font-bold"
          style={{ background: "var(--primary)", color: "var(--bg-page)" }}
        >
          3
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[0.72rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            No github.com, cada <strong style={{ color: "var(--text)" }}>Commit changes</strong> já publica o arquivo.
            No github.dev: ícone de <strong style={{ color: "var(--text)" }}>Source Control</strong> → mensagem{" "}
            <code className="font-mono text-[0.66rem]" style={{ color: "var(--primary)" }}>
              feat: seletor de temas
            </code>{" "}
            → <strong style={{ color: "var(--text)" }}>Commit &amp; Push</strong> (tudo de uma vez). Depois, onde o
            banco roda (VPS ou seu ambiente), gere a migration:
          </p>
          <div
            className="mt-1 flex items-center gap-2 overflow-x-auto rounded-[var(--radius-sm)] border px-2.5 py-1.5"
            style={{ background: "color-mix(in oklab, var(--text) 5%, var(--bg-page))", borderColor: "var(--border)" }}
          >
            <code className="whitespace-nowrap font-mono text-[0.7rem]" style={{ color: "var(--text)" }}>
              <span style={{ color: "var(--primary)" }}>$ </span>
              {ALEMBIC_CMD}
            </code>
            <button
              type="button"
              onClick={() => copyCmd(ALEMBIC_CMD)}
              className="ml-auto shrink-0 rounded p-1 transition-all duration-150 hover:-translate-y-px"
              style={{ color: cmdCopied ? "var(--success)" : "var(--muted)" }}
              aria-label={cmdCopied ? "Copiado" : "Copiar comando do Alembic"}
            >
              {cmdCopied ? <CheckIcon /> : <CopyIcon />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Simulador de instalação                                             */
/* ------------------------------------------------------------------ */

type SimLine = { text: string; tone: "cmd" | "head" | "ok" | "ask" | "out" };

const SIM_LINES: SimLine[] = [
  { text: "$ ./aplicar_patch.sh", tone: "cmd" },
  { text: "==> Repositório: ~/ControleRendaVariavel", tone: "head" },
  { text: "==> [1/4] Criando arquivos novos...", tone: "head" },
  { text: "    criado   app/themes.py", tone: "ok" },
  { text: "    criado   app/routes/profile.py", tone: "ok" },
  { text: "    criado   app/templates/profile.html", tone: "ok" },
  { text: "    criado   app/static/theme_system.css", tone: "ok" },
  { text: "==> [2/4] Edições protegidas...", tone: "head" },
  { text: "    editado  app/models.py: campo ui_theme", tone: "ok" },
  { text: "    editado  base.html: data-theme + theme_system.css", tone: "ok" },
  { text: "    editado  routes/__init__.py: from . import profile", tone: "ok" },
  { text: "==> [3/4] alembic upgrade head ... migration aplicada", tone: "head" },
  { text: "==> [4/4] Commitar tudo e dar push agora? [s/N] s", tone: "ask" },
  { text: "    [main a1b2c3d] feat: seletor de temas — 13 temas", tone: "out" },
  { text: "    → github.com:MSPA-Coder/ControleRendaVariavel.git", tone: "out" },
  { text: "==> Publicado! Faça login e abra /profile para escolher o tema.", tone: "ok" },
];

const TONE_COLOR: Record<SimLine["tone"], string> = {
  cmd: "var(--text)",
  head: "var(--primary)",
  ok: "var(--success)",
  ask: "var(--warning)",
  out: "var(--muted)",
};

function InstallSimulator() {
  const [visible, setVisible] = useState(0);
  const [running, setRunning] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const done = visible >= SIM_LINES.length;

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const tick = (from: number) => {
    if (from >= SIM_LINES.length) {
      setRunning(false);
      return;
    }
    timer.current = window.setTimeout(() => {
      setVisible(from + 1);
      tick(from + 1);
    }, from === 0 ? 260 : 300);
  };

  const start = () => {
    if (running) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setVisible(SIM_LINES.length);
      return;
    }
    setRunning(true);
    setVisible(0);
    tick(0);
  };

  return (
    <div
      className="flex h-full flex-col overflow-hidden rounded-[var(--radius-lg)] border"
      style={{ background: "color-mix(in oklab, var(--text) 4%, var(--card))", borderColor: "var(--border)" }}
    >
      <div
        className="flex items-center gap-2.5 border-b px-4 py-2"
        style={{ borderColor: "var(--border)", background: "var(--bg-header)" }}
      >
        <TerminalIcon className="text-[0.95rem]" style={{ color: "var(--primary)" }} />
        <span className="font-mono text-[0.68rem] font-semibold uppercase tracking-[0.14em]" style={{ color: "var(--muted)" }}>
          o instalador em ação — simulação
        </span>
        <div className="ml-auto flex gap-1.5">
          <button
            type="button"
            onClick={start}
            disabled={running}
            className="rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.64rem] font-bold uppercase tracking-wider transition-all duration-150 enabled:hover:-translate-y-px disabled:opacity-60"
            style={{
              background: done ? "var(--card)" : "var(--primary)",
              borderColor: done ? "var(--border)" : "var(--primary)",
              color: done ? "var(--text)" : "var(--bg-page)",
            }}
          >
            {done ? "↺ rodar de novo" : running ? "rodando…" : "▶ simular"}
          </button>
          {visible > 0 && !running && (
            <button
              type="button"
              onClick={() => setVisible(0)}
              className="rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.64rem] uppercase tracking-wider transition-colors"
              style={{ borderColor: "var(--border)", color: "var(--muted)", background: "var(--card)" }}
            >
              limpar
            </button>
          )}
        </div>
      </div>
      <div className="min-h-[190px] flex-1 px-4 py-3 font-mono text-[0.7rem] leading-[1.85]">
        {visible === 0 && !running && (
          <p style={{ color: "var(--muted)" }}>
            <span className="caret mr-1 inline-block h-[1em] w-[0.5em] translate-y-[0.15em]" style={{ background: "var(--primary)" }} />
            Clique em <strong style={{ color: "var(--primary)" }}>▶ simular</strong> para ver, linha a linha, o que o
            script executa no seu clone — sem risco, sem tocar em nada.
          </p>
        )}
        {SIM_LINES.slice(0, visible).map((l, i) => (
          <p key={i} className="toast-in whitespace-pre" style={{ color: TONE_COLOR[l.tone], fontWeight: l.tone === "cmd" ? 600 : 400 }}>
            {l.text}
          </p>
        ))}
        {running && <span className="caret inline-block h-[1em] w-[0.5em] translate-y-[0.15em]" style={{ background: "var(--primary)" }} />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Modo Windows — .bat apontando para o destino do usuário             */
/* ------------------------------------------------------------------ */

function WindowsMode({ onDownload, zipping }: { onDownload: () => void; zipping: boolean }) {
  const [pathCopied, copyPath] = useCopy();
  return (
    <div className="flex min-w-0 flex-col gap-3.5">
      {/* destino */}
      <div>
        <p className="text-[0.72rem] font-medium" style={{ color: "var(--muted)" }}>
          O <code className="font-mono" style={{ color: "var(--primary)" }}>aplicar_patch.bat</code> já vem apontando
          para o seu repositório:
        </p>
        <div
          className="mt-1 flex items-center gap-2 overflow-x-auto rounded-[var(--radius-sm)] border px-2.5 py-1.5"
          style={{ background: "color-mix(in oklab, var(--text) 5%, var(--bg-page))", borderColor: "var(--border)" }}
        >
          <code className="whitespace-nowrap font-mono text-[0.66rem]" style={{ color: "var(--text)" }}>
            {WIN_TARGET}
          </code>
          <button
            type="button"
            onClick={() => copyPath(WIN_TARGET)}
            className="ml-auto shrink-0 rounded p-1 transition-all duration-150 hover:-translate-y-px"
            style={{ color: pathCopied ? "var(--success)" : "var(--muted)" }}
            aria-label={pathCopied ? "Copiado" : "Copiar caminho"}
          >
            {pathCopied ? <CheckIcon /> : <CopyIcon />}
          </button>
        </div>
      </div>

      {/* 3 passos */}
      <ol className="flex flex-col gap-2">
        {[
          "Baixe o ZIP (botão acima) — ele cai em C:\\Users\\MSPA\\Downloads",
          "Extraia o ZIP em qualquer pasta (ex.: clique com o direito > Extrair tudo)",
          "Dê dois cliques em aplicar_patch.bat e responda às perguntas",
        ].map((s, i) => (
          <li key={i} className="flex items-start gap-2.5">
            <span
              className="mt-0.5 flex h-5.5 w-5.5 min-h-[1.375rem] min-w-[1.375rem] shrink-0 items-center justify-center rounded-full font-display text-[0.66rem] font-bold"
              style={{ background: "var(--primary)", color: "var(--bg-page)" }}
            >
              {i + 1}
            </span>
            <span className="text-[0.74rem] leading-relaxed" style={{ color: "var(--text)" }}>
              {s}
            </span>
          </li>
        ))}
      </ol>

      <button
        type="button"
        onClick={onDownload}
        disabled={zipping}
        className="inline-flex items-center justify-center gap-2 self-start rounded-[var(--radius-sm)] border px-4 py-2 font-mono text-[0.74rem] font-bold transition-all duration-200 enabled:hover:-translate-y-px disabled:opacity-70"
        style={{
          background: "var(--primary)",
          borderColor: "var(--primary)",
          color: "var(--bg-page)",
          boxShadow: "0 6px 18px -8px color-mix(in oklab, var(--primary) 70%, transparent)",
        }}
      >
        <DownloadIcon />
        {zipping ? "gerando…" : "Baixar temas-crv-patch-win.zip"}
      </button>

      <p className="text-[0.66rem] leading-relaxed" style={{ color: "var(--muted)" }}>
        O <strong style={{ color: "var(--text)" }}>.bat</strong> copia os 4 arquivos novos, roda as edições protegidas
        (via <code className="font-mono" style={{ color: "var(--primary)" }}>editar.py</code>), gera a migration e —
        com sua confirmação — commita e faz o push. Se o Windows bloquear, clique em{" "}
        <em>"Mais informações" → "Executar assim mesmo"</em>. Se o caminho acima mudar, ele pede para você arrastar a
        pasta do repositório para a janela.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Seção                                                               */
/* ------------------------------------------------------------------ */

export function PatchSection() {
  const files = useMemo(() => buildPatchFiles(), []);
  const [active, setActive] = useState(0);
  const [copied, copy] = useCopy();
  const [zipping, setZipping] = useState(false);
  const [mode, setMode] = useState<"win" | "terminal" | "browser">("win");
  const file = files[active];
  const lines = useMemo(() => file.content.split("\n").length, [file]);

  const downloadZip = async () => {
    setZipping(true);
    try {
      if (mode === "win") await downloadWindowsPatchZip();
      else await downloadPatchZip();
    } finally {
      setTimeout(() => setZipping(false), 600);
    }
  };

  return (
    <div>
      {/* meta bar — procedência + zip */}
      <Reveal>
        <div
          className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[var(--radius)] border px-4 py-3"
          style={{ background: "var(--card)", borderColor: "var(--border)" }}
        >
          <a
            href="https://github.com/MSPA-Coder/ControleRendaVariavel"
            target="_blank"
            rel="noreferrer"
            className="group inline-flex items-center gap-2 font-mono text-[0.78rem] font-semibold"
            style={{ color: "var(--text)" }}
          >
            <GitHubIcon className="text-[1.05rem]" />
            MSPA-Coder/ControleRendaVariavel
            <span className="rounded border px-1.5 py-px text-[0.62rem]" style={{ borderColor: "var(--border)", color: "var(--muted)" }}>
              main
            </span>
            <ArrowUpRightIcon className="text-[0.7rem] opacity-50 transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
          </a>
          <span className="hidden h-4 w-px sm:block" style={{ background: "var(--border)" }} />
          <span className="inline-flex items-center gap-1.5 font-mono text-[0.68rem]" style={{ color: "var(--muted)" }}>
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--success)" }} />
            app.css + models.py lidos do GitHub
          </span>
          <button
            type="button"
            onClick={downloadZip}
            disabled={zipping}
            className="ml-auto inline-flex items-center gap-2 rounded-[var(--radius-sm)] border px-3.5 py-1.5 font-mono text-[0.72rem] font-bold transition-all duration-200 enabled:hover:-translate-y-px disabled:opacity-70"
            style={{
              background: "var(--primary)",
              borderColor: "var(--primary)",
              color: "var(--bg-page)",
              boxShadow: "0 6px 18px -8px color-mix(in oklab, var(--primary) 70%, transparent)",
            }}
          >
            <DownloadIcon />
            {zipping ? "gerando…" : mode === "win" ? "Baixar ZIP Windows (.bat)" : "Baixar tudo (.zip)"}
          </button>
        </div>
      </Reveal>

      {/* como aplicar: terminal ou navegador + simulador */}
      <div className="mb-4 grid gap-3 lg:grid-cols-[1fr_1.1fr]">
        <Reveal delay={60}>
          <div
            className="flex h-full flex-col gap-4 rounded-[var(--radius-lg)] border p-4 sm:p-5"
            style={{ background: "var(--card)", borderColor: "var(--border)" }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-[0.95rem] font-bold" style={{ color: "var(--text)" }}>
                  Como aplicar
                </h3>
                <p className="mt-0.5 text-[0.72rem] leading-relaxed" style={{ color: "var(--muted)" }}>
                  No Windows com um .bat, com 3 comandos no terminal ou direto pelo navegador.
                </p>
              </div>
              {/* seletor de modo */}
              <div
                className="flex rounded-[var(--radius)] border p-0.5"
                style={{ borderColor: "var(--border)", background: "var(--bg-page)" }}
                role="tablist"
                aria-label="Modo de aplicação do patch"
              >
                {(
                  [
                    { id: "win", label: "Windows", icon: <DownloadIcon className="text-[0.85rem]" /> },
                    { id: "terminal", label: "Terminal", icon: <TerminalIcon className="text-[0.85rem]" /> },
                    { id: "browser", label: "Navegador", icon: <BrowserIcon className="text-[0.85rem]" /> },
                  ] as const
                ).map((m) => {
                  const on = mode === m.id;
                  return (
                    <button
                      key={m.id}
                      type="button"
                      role="tab"
                      aria-selected={on}
                      onClick={() => setMode(m.id)}
                      className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] px-3 py-1.5 font-mono text-[0.68rem] font-bold uppercase tracking-wider transition-all duration-200"
                      style={
                        on
                          ? { background: "var(--primary)", color: "var(--bg-page)", boxShadow: "0 4px 12px -6px color-mix(in oklab, var(--primary) 70%, transparent)" }
                          : { color: "var(--muted)" }
                      }
                    >
                      {m.icon}
                      {m.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {mode === "terminal" ? (
              <div className="flex flex-col gap-3.5">
                {QUICK_STEPS.map((s) => (
                  <QuickStep key={s.n} step={s} />
                ))}
                <p className="text-[0.66rem] leading-relaxed" style={{ color: "var(--muted)" }}>
                  O zip já traz os arquivos nos caminhos certos e o{" "}
                  <code className="font-mono" style={{ color: "var(--primary)" }}>aplicar_patch.sh</code>: ele cria,
                  edita, migra e — <strong style={{ color: "var(--text)" }}>só com o seu "s"</strong> — commita e faz o
                  push.
                </p>
              </div>
            ) : mode === "win" ? (
              <WindowsMode onDownload={downloadZip} zipping={zipping} />
            ) : (
              <BrowserMode files={files} onOpen={setActive} />
            )}
          </div>
        </Reveal>
        <Reveal delay={120}>
          <InstallSimulator />
        </Reveal>
      </div>

      <Reveal delay={80}>
        <div className="grid gap-3 lg:grid-cols-[280px_1fr]">
          {/* lista de arquivos */}
          <nav aria-label="Arquivos do patch" className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
            {files.map((f, i) => {
              const on = i === active;
              const ks = KIND_STYLE[f.kind];
              return (
                <button
                  key={f.path}
                  type="button"
                  onClick={() => setActive(i)}
                  className="shrink-0 rounded-[var(--radius)] border px-3 py-2.5 text-left transition-all duration-200 lg:shrink"
                  style={{
                    minWidth: "190px",
                    background: on ? "var(--primary-light)" : "var(--card)",
                    borderColor: on ? "var(--primary)" : "var(--border)",
                  }}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span
                      className="truncate font-mono text-[0.72rem] font-semibold"
                      style={{ color: on ? "var(--primary)" : "var(--text)" }}
                    >
                      {f.path}
                    </span>
                    <span
                      className="rounded px-1.5 py-px font-mono text-[0.56rem] font-bold tracking-wider"
                      style={{
                        color: ks.color,
                        background: `color-mix(in oklab, ${ks.color} 13%, transparent)`,
                      }}
                    >
                      {ks.label}
                    </span>
                  </span>
                  <span className="mt-0.5 hidden text-[0.66rem] leading-snug lg:block" style={{ color: "var(--muted)" }}>
                    {f.desc}
                  </span>
                </button>
              );
            })}
          </nav>

          {/* viewer */}
          <div
            className="flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-lg)] border"
            style={{ background: "var(--card)", borderColor: "var(--border)" }}
          >
            <div
              className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5"
              style={{ background: "var(--bg-header)", borderColor: "var(--border)" }}
            >
              <span className="font-mono text-[0.74rem] font-bold" style={{ color: "var(--text)" }}>
                {file.path}
              </span>
              <span className="font-mono text-[0.64rem]" style={{ color: "var(--muted)" }}>
                {lines} linhas
              </span>
              <span className="ml-auto flex gap-1.5">
                <button
                  type="button"
                  onClick={() => copy(file.content)}
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.66rem] font-semibold transition-all duration-150 hover:-translate-y-px"
                  style={{
                    borderColor: "var(--border)",
                    background: "var(--card)",
                    color: copied ? "var(--success)" : "var(--muted)",
                  }}
                >
                  {copied ? <CheckIcon /> : <CopyIcon />}
                  {copied ? "copiado" : "copiar"}
                </button>
                <button
                  type="button"
                  onClick={() => saveText(file.path, file.content)}
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.66rem] font-semibold transition-all duration-150 hover:-translate-y-px"
                  style={{ borderColor: "var(--border)", background: "var(--card)", color: "var(--muted)" }}
                >
                  <DownloadIcon />
                  baixar
                </button>
              </span>
            </div>
            <pre className="code-block max-h-[520px] flex-1 overflow-auto rounded-none border-0">
              <code>{file.content}</code>
            </pre>
            <div
              className="border-t px-4 py-2 font-mono text-[0.64rem]"
              style={{ borderColor: "var(--border)", color: "var(--muted)" }}
            >
              {file.desc}
            </div>
          </div>
        </div>
      </Reveal>

      <Reveal delay={140}>
        <p className="mt-3 text-[0.74rem] leading-relaxed" style={{ color: "var(--muted)" }}>
          O instalador segue a ordem <strong style={{ color: "var(--text)" }}>themes.py → models.py → routes →
          templates → theme_system.css → migration</strong>. Como o CSS redefine apenas as variáveis que o app já usa
          (<code className="font-mono" style={{ color: "var(--primary)" }}>--bg</code>,{" "}
          <code className="font-mono" style={{ color: "var(--primary)" }}>--surface</code>,{" "}
          <code className="font-mono" style={{ color: "var(--primary)" }}>--accent</code>…), todos os 13 temas —
          inclusive o <strong style={{ color: "var(--text)" }}>Original RV</strong>, que replica o visual de hoje —
          funcionam sem mexer em nenhuma regra existente.
        </p>
      </Reveal>
    </div>
  );
}


+++ src/components/PatchSection.tsx (修改后)
import { useEffect, useMemo, useRef, useState } from "react";
import { buildPatchFiles, WIN_TARGET, type PatchFile } from "../lib/patch";
import { downloadPatchZip, downloadWindowsPatchZip, saveText } from "../lib/download";
import { reportFramedDownload } from "../lib/downloadGate";
import { useCopy } from "../hooks/useCopy";
import {
  ArrowUpRightIcon,
  BrowserIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  GitHubIcon,
  TerminalIcon,
} from "./icons";
import { Reveal } from "./Reveal";

const KIND_STYLE: Record<PatchFile["kind"], { label: string; color: string }> = {
  novo: { label: "NOVO", color: "var(--success)" },
  edicao: { label: "EDIÇÃO", color: "var(--warning)" },
  comando: { label: "TERMINAL", color: "var(--primary)" },
};

/* ------------------------------------------------------------------ */
/* Quick start — modo terminal (3 comandos)                            */
/* ------------------------------------------------------------------ */

const QUICK_STEPS: { n: string; desc: string; cmd: string }[] = [
  {
    n: "1",
    desc: "Clone (ou entre na pasta que já existe)",
    cmd: "git clone https://github.com/MSPA-Coder/ControleRendaVariavel.git && cd ControleRendaVariavel",
  },
  {
    n: "2",
    desc: "Extraia o .zip na raiz do repositório (os caminhos já estão prontos)",
    cmd: "unzip -o temas-crv-patch.zip",
  },
  {
    n: "3",
    desc: "Rode o instalador e confirme o push no final",
    cmd: "chmod +x aplicar_patch.sh && ./aplicar_patch.sh",
  },
];

function QuickStep({ step }: { step: (typeof QUICK_STEPS)[number] }) {
  const [copied, copy] = useCopy();
  return (
    <div className="flex min-w-0 items-start gap-3">
      <span
        className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.72rem] font-bold"
        style={{ background: "var(--primary)", color: "var(--bg-page)" }}
      >
        {step.n}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[0.72rem] font-medium" style={{ color: "var(--muted)" }}>
          {step.desc}
        </p>
        <div
          className="group mt-1 flex items-center gap-2 overflow-x-auto rounded-[var(--radius-sm)] border px-2.5 py-1.5"
          style={{ background: "color-mix(in oklab, var(--text) 5%, var(--bg-page))", borderColor: "var(--border)" }}
        >
          <code className="whitespace-nowrap font-mono text-[0.7rem]" style={{ color: "var(--text)" }}>
            <span style={{ color: "var(--primary)" }}>$ </span>
            {step.cmd}
          </code>
          <button
            type="button"
            onClick={() => copy(step.cmd)}
            className="ml-auto shrink-0 rounded p-1 transition-all duration-150 hover:-translate-y-px"
            style={{ color: copied ? "var(--success)" : "var(--muted)" }}
            title={copied ? "Copiado!" : "Copiar comando"}
            aria-label={copied ? "Copiado" : `Copiar passo ${step.n}`}
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Modo navegador — github.dev + checklist interativo                  */
/* ------------------------------------------------------------------ */

const CHECKLIST_KEY = "crv-patch-checklist";
const ALEMBIC_CMD = 'alembic revision --autogenerate -m "add users.ui_theme" && alembic upgrade head';

function CheckRow({
  file,
  checked,
  onToggle,
  onOpen,
}: {
  file: PatchFile;
  checked: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  const [copied, copy] = useCopy();
  const ks = KIND_STYLE[file.kind];
  return (
    <li
      className="flex items-center gap-2.5 rounded-[var(--radius-sm)] border px-2.5 py-1.5 transition-colors duration-150"
      style={{
        borderColor: checked ? "color-mix(in oklab, var(--success) 45%, var(--border))" : "var(--border)",
        background: checked ? "color-mix(in oklab, var(--success) 6%, var(--card))" : "var(--card)",
        opacity: checked ? 0.8 : 1,
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-label={checked ? `Desmarcar ${file.path}` : `Marcar ${file.path} como colado`}
        className="flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded border transition-all duration-150"
        style={{
          width: "1.15rem",
          height: "1.15rem",
          borderColor: checked ? "var(--success)" : "var(--border)",
          background: checked ? "var(--success)" : "transparent",
          color: "var(--bg-page)",
        }}
      >
        {checked && <CheckIcon className="text-[0.66rem]" />}
      </button>
      <code
        className="min-w-0 flex-1 truncate font-mono text-[0.7rem] font-semibold"
        style={{ color: checked ? "var(--muted)" : "var(--text)", textDecoration: checked ? "line-through" : "none" }}
      >
        {file.path}
      </code>
      <span
        className="hidden rounded px-1.5 py-px font-mono text-[0.55rem] font-bold tracking-wider sm:inline-block"
        style={{ color: ks.color, background: `color-mix(in oklab, ${ks.color} 13%, transparent)` }}
      >
        {ks.label}
      </span>
      <button
        type="button"
        onClick={() => {
          copy(file.content);
          onOpen();
        }}
        className="inline-flex shrink-0 items-center gap-1 rounded-[var(--radius-sm)] border px-2 py-0.5 font-mono text-[0.62rem] font-semibold transition-all duration-150 hover:-translate-y-px"
        style={{
          borderColor: "var(--border)",
          background: copied ? "color-mix(in oklab, var(--success) 10%, var(--card))" : "var(--bg-page)",
          color: copied ? "var(--success)" : "var(--primary)",
        }}
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
        {copied ? "copiado" : "copiar"}
      </button>
    </li>
  );
}

function BrowserMode({
  files,
  onOpen,
}: {
  files: PatchFile[];
  onOpen: (index: number) => void;
}) {
  const checklist = useMemo(
    () => files.filter((f) => !["aplicar_patch.sh", "aplicar_patch.bat", "editar.py"].includes(f.path)),
    [files],
  );
  const [done, setDone] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem(CHECKLIST_KEY) || "{}");
    } catch {
      return {};
    }
  });
  const [cmdCopied, copyCmd] = useCopy();

  useEffect(() => {
    try {
      localStorage.setItem(CHECKLIST_KEY, JSON.stringify(done));
    } catch {
      /* armazenamento indisponível */
    }
  }, [done]);

  const doneCount = checklist.filter((f) => done[f.path]).length;
  const progress = checklist.length ? doneCount / checklist.length : 0;

  return (
    <div className="flex min-w-0 flex-col gap-3.5">
      {/* passo 1 */}
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.72rem] font-bold"
          style={{ background: "var(--primary)", color: "var(--bg-page)" }}
        >
          1
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[0.72rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            Abra o repositório <strong style={{ color: "var(--text)" }}>logado na sua conta</strong> e use os botões do
            próprio GitHub — sem download, sem terminal, sem editor. Cada{" "}
            <strong style={{ color: "var(--text)" }}>Commit changes</strong> publica o arquivo na hora:
          </p>
          <a
            href="https://github.com/MSPA-Coder/ControleRendaVariavel"
            target="_blank"
            rel="noreferrer"
            className="group mt-1.5 inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.68rem] font-semibold transition-all duration-150 hover:-translate-y-px"
            style={{ borderColor: "var(--primary)", color: "var(--primary)", background: "var(--primary-light)" }}
          >
            <GitHubIcon className="text-[0.85rem]" />
            github.com/MSPA-Coder/ControleRendaVariavel
            <ArrowUpRightIcon className="text-[0.62rem] transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
          </a>
          <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
            <div
              className="rounded-[var(--radius-sm)] border p-2.5"
              style={{ borderColor: "var(--border)", background: "color-mix(in oklab, var(--text) 3%, var(--card))" }}
            >
              <span
                className="rounded px-1.5 py-px font-mono text-[0.56rem] font-bold tracking-wider"
                style={{ color: "var(--success)", background: "color-mix(in oklab, var(--success) 13%, transparent)" }}
              >
                4× NOVO
              </span>
              <p className="mt-1.5 text-[0.68rem] leading-relaxed" style={{ color: "var(--muted)" }}>
                <strong style={{ color: "var(--text)" }}>Add file</strong> ▸{" "}
                <strong style={{ color: "var(--text)" }}>Create new file</strong> ▸ digite o caminho (ex.:{" "}
                <code className="font-mono text-[0.64rem]" style={{ color: "var(--primary)" }}>
                  app/themes.py
                </code>
                ) ▸ cole o conteúdo ▸ <strong style={{ color: "var(--text)" }}>Commit changes</strong>
              </p>
            </div>
            <div
              className="rounded-[var(--radius-sm)] border p-2.5"
              style={{ borderColor: "var(--border)", background: "color-mix(in oklab, var(--text) 3%, var(--card))" }}
            >
              <span
                className="rounded px-1.5 py-px font-mono text-[0.56rem] font-bold tracking-wider"
                style={{ color: "var(--warning)", background: "color-mix(in oklab, var(--warning) 13%, transparent)" }}
              >
                3× EDIÇÃO
              </span>
              <p className="mt-1.5 text-[0.68rem] leading-relaxed" style={{ color: "var(--muted)" }}>
                abra o arquivo (ex.:{" "}
                <code className="font-mono text-[0.64rem]" style={{ color: "var(--primary)" }}>
                  app/models.py
                </code>
                ) ▸ ícone do <strong style={{ color: "var(--text)" }}>lápis</strong> ▸ acrescente as linhas com{" "}
                <code className="font-mono text-[0.64rem]" style={{ color: "var(--success)" }}>+</code> ▸{" "}
                <strong style={{ color: "var(--text)" }}>Commit changes</strong>
              </p>
            </div>
          </div>
          <p className="mt-2 text-[0.64rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            Prefere um editor de verdade? Pressione a tecla{" "}
            <kbd
              className="rounded border px-1.5 py-px font-mono text-[0.62rem] font-bold"
              style={{ borderColor: "var(--border)", background: "var(--bg-page)", color: "var(--text)" }}
            >
              .
            </kbd>{" "}
            na página do repositório e edite tudo no{" "}
            <a
              href="https://github.dev/MSPA-Coder/ControleRendaVariavel"
              target="_blank"
              rel="noreferrer"
              className="font-semibold underline decoration-dotted underline-offset-2 transition-opacity hover:opacity-70"
              style={{ color: "var(--primary)" }}
            >
              github.dev
            </a>{" "}
            com um commit só no final.
          </p>
        </div>
      </div>

      {/* passo 2 — checklist */}
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.72rem] font-bold"
          style={{ background: "var(--primary)", color: "var(--bg-page)" }}
        >
          2
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <p className="text-[0.72rem] font-medium" style={{ color: "var(--muted)" }}>
              Em cada arquivo: <strong style={{ color: "var(--text)" }}>copie aqui → crie ou edite no GitHub (passo
              1)</strong>. Marque conforme for commitando:
            </p>
            <span
              className="rounded-full border px-2 py-px font-mono text-[0.62rem] font-bold"
              style={{
                borderColor: progress === 1 ? "var(--success)" : "var(--border)",
                color: progress === 1 ? "var(--success)" : "var(--primary)",
              }}
            >
              {doneCount}/{checklist.length} colados
            </span>
          </div>
          <div
            className="mt-1.5 h-1 overflow-hidden rounded-full"
            style={{ background: "color-mix(in oklab, var(--border) 70%, transparent)" }}
          >
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${progress * 100}%`, background: progress === 1 ? "var(--success)" : "var(--primary)" }}
            />
          </div>
          <ul className="mt-2 flex flex-col gap-1.5">
            {checklist.map((f) => (
              <CheckRow
                key={f.path}
                file={f}
                checked={!!done[f.path]}
                onToggle={() => setDone((d) => ({ ...d, [f.path]: !d[f.path] }))}
                onOpen={() => onOpen(files.findIndex((x) => x.path === f.path))}
              />
            ))}
          </ul>
          <p className="mt-1.5 text-[0.64rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            Arquivos <strong style={{ color: "var(--warning)" }}>EDIÇÃO</strong> não substituem o arquivo: procure o
            trecho indicado e acrescente o que está com{" "}
            <code className="font-mono" style={{ color: "var(--success)" }}>+</code> no início da linha.
          </p>
        </div>
      </div>

      {/* passo 3 */}
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-[0.72rem] font-bold"
          style={{ background: "var(--primary)", color: "var(--bg-page)" }}
        >
          3
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[0.72rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            No github.com, cada <strong style={{ color: "var(--text)" }}>Commit changes</strong> já publica o arquivo.
            No github.dev: ícone de <strong style={{ color: "var(--text)" }}>Source Control</strong> → mensagem{" "}
            <code className="font-mono text-[0.66rem]" style={{ color: "var(--primary)" }}>
              feat: seletor de temas
            </code>{" "}
            → <strong style={{ color: "var(--text)" }}>Commit &amp; Push</strong> (tudo de uma vez). Depois, onde o
            banco roda (VPS ou seu ambiente), gere a migration:
          </p>
          <div
            className="mt-1 flex items-center gap-2 overflow-x-auto rounded-[var(--radius-sm)] border px-2.5 py-1.5"
            style={{ background: "color-mix(in oklab, var(--text) 5%, var(--bg-page))", borderColor: "var(--border)" }}
          >
            <code className="whitespace-nowrap font-mono text-[0.7rem]" style={{ color: "var(--text)" }}>
              <span style={{ color: "var(--primary)" }}>$ </span>
              {ALEMBIC_CMD}
            </code>
            <button
              type="button"
              onClick={() => copyCmd(ALEMBIC_CMD)}
              className="ml-auto shrink-0 rounded p-1 transition-all duration-150 hover:-translate-y-px"
              style={{ color: cmdCopied ? "var(--success)" : "var(--muted)" }}
              aria-label={cmdCopied ? "Copiado" : "Copiar comando do Alembic"}
            >
              {cmdCopied ? <CheckIcon /> : <CopyIcon />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Simulador de instalação                                             */
/* ------------------------------------------------------------------ */

type SimLine = { text: string; tone: "cmd" | "head" | "ok" | "ask" | "out" };

const SIM_LINES: SimLine[] = [
  { text: "$ ./aplicar_patch.sh", tone: "cmd" },
  { text: "==> Repositório: ~/ControleRendaVariavel", tone: "head" },
  { text: "==> [1/4] Criando arquivos novos...", tone: "head" },
  { text: "    criado   app/themes.py", tone: "ok" },
  { text: "    criado   app/routes/profile.py", tone: "ok" },
  { text: "    criado   app/templates/profile.html", tone: "ok" },
  { text: "    criado   app/static/theme_system.css", tone: "ok" },
  { text: "==> [2/4] Edições protegidas...", tone: "head" },
  { text: "    editado  app/models.py: campo ui_theme", tone: "ok" },
  { text: "    editado  base.html: data-theme + theme_system.css", tone: "ok" },
  { text: "    editado  routes/__init__.py: from . import profile", tone: "ok" },
  { text: "==> [3/4] alembic upgrade head ... migration aplicada", tone: "head" },
  { text: "==> [4/4] Commitar tudo e dar push agora? [s/N] s", tone: "ask" },
  { text: "    [main a1b2c3d] feat: seletor de temas — 13 temas", tone: "out" },
  { text: "    → github.com:MSPA-Coder/ControleRendaVariavel.git", tone: "out" },
  { text: "==> Publicado! Faça login e abra /profile para escolher o tema.", tone: "ok" },
];

const TONE_COLOR: Record<SimLine["tone"], string> = {
  cmd: "var(--text)",
  head: "var(--primary)",
  ok: "var(--success)",
  ask: "var(--warning)",
  out: "var(--muted)",
};

function InstallSimulator() {
  const [visible, setVisible] = useState(0);
  const [running, setRunning] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const done = visible >= SIM_LINES.length;

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const tick = (from: number) => {
    if (from >= SIM_LINES.length) {
      setRunning(false);
      return;
    }
    timer.current = window.setTimeout(() => {
      setVisible(from + 1);
      tick(from + 1);
    }, from === 0 ? 260 : 300);
  };

  const start = () => {
    if (running) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setVisible(SIM_LINES.length);
      return;
    }
    setRunning(true);
    setVisible(0);
    tick(0);
  };

  return (
    <div
      className="flex h-full flex-col overflow-hidden rounded-[var(--radius-lg)] border"
      style={{ background: "color-mix(in oklab, var(--text) 4%, var(--card))", borderColor: "var(--border)" }}
    >
      <div
        className="flex items-center gap-2.5 border-b px-4 py-2"
        style={{ borderColor: "var(--border)", background: "var(--bg-header)" }}
      >
        <TerminalIcon className="text-[0.95rem]" style={{ color: "var(--primary)" }} />
        <span className="font-mono text-[0.68rem] font-semibold uppercase tracking-[0.14em]" style={{ color: "var(--muted)" }}>
          o instalador em ação — simulação
        </span>
        <div className="ml-auto flex gap-1.5">
          <button
            type="button"
            onClick={start}
            disabled={running}
            className="rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.64rem] font-bold uppercase tracking-wider transition-all duration-150 enabled:hover:-translate-y-px disabled:opacity-60"
            style={{
              background: done ? "var(--card)" : "var(--primary)",
              borderColor: done ? "var(--border)" : "var(--primary)",
              color: done ? "var(--text)" : "var(--bg-page)",
            }}
          >
            {done ? "↺ rodar de novo" : running ? "rodando…" : "▶ simular"}
          </button>
          {visible > 0 && !running && (
            <button
              type="button"
              onClick={() => setVisible(0)}
              className="rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.64rem] uppercase tracking-wider transition-colors"
              style={{ borderColor: "var(--border)", color: "var(--muted)", background: "var(--card)" }}
            >
              limpar
            </button>
          )}
        </div>
      </div>
      <div className="min-h-[190px] flex-1 px-4 py-3 font-mono text-[0.7rem] leading-[1.85]">
        {visible === 0 && !running && (
          <p style={{ color: "var(--muted)" }}>
            <span className="caret mr-1 inline-block h-[1em] w-[0.5em] translate-y-[0.15em]" style={{ background: "var(--primary)" }} />
            Clique em <strong style={{ color: "var(--primary)" }}>▶ simular</strong> para ver, linha a linha, o que o
            script executa no seu clone — sem risco, sem tocar em nada.
          </p>
        )}
        {SIM_LINES.slice(0, visible).map((l, i) => (
          <p key={i} className="toast-in whitespace-pre" style={{ color: TONE_COLOR[l.tone], fontWeight: l.tone === "cmd" ? 600 : 400 }}>
            {l.text}
          </p>
        ))}
        {running && <span className="caret inline-block h-[1em] w-[0.5em] translate-y-[0.15em]" style={{ background: "var(--primary)" }} />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Modo Windows — .bat apontando para o destino do usuário             */
/* ------------------------------------------------------------------ */

function WindowsMode({ onDownload, zipping }: { onDownload: () => void; zipping: boolean }) {
  const [pathCopied, copyPath] = useCopy();
  return (
    <div className="flex min-w-0 flex-col gap-3.5">
      {/* destino */}
      <div>
        <p className="text-[0.72rem] font-medium" style={{ color: "var(--muted)" }}>
          O <code className="font-mono" style={{ color: "var(--primary)" }}>aplicar_patch.bat</code> já vem apontando
          para o seu repositório:
        </p>
        <div
          className="mt-1 flex items-center gap-2 overflow-x-auto rounded-[var(--radius-sm)] border px-2.5 py-1.5"
          style={{ background: "color-mix(in oklab, var(--text) 5%, var(--bg-page))", borderColor: "var(--border)" }}
        >
          <code className="whitespace-nowrap font-mono text-[0.66rem]" style={{ color: "var(--text)" }}>
            {WIN_TARGET}
          </code>
          <button
            type="button"
            onClick={() => copyPath(WIN_TARGET)}
            className="ml-auto shrink-0 rounded p-1 transition-all duration-150 hover:-translate-y-px"
            style={{ color: pathCopied ? "var(--success)" : "var(--muted)" }}
            aria-label={pathCopied ? "Copiado" : "Copiar caminho"}
          >
            {pathCopied ? <CheckIcon /> : <CopyIcon />}
          </button>
        </div>
      </div>

      {/* 3 passos */}
      <ol className="flex flex-col gap-2">
        {[
          "Baixe o ZIP (botão acima) — ele cai em C:\\Users\\MSPA\\Downloads",
          "Extraia o ZIP em qualquer pasta (ex.: clique com o direito > Extrair tudo)",
          "Dê dois cliques em aplicar_patch.bat e responda às perguntas",
        ].map((s, i) => (
          <li key={i} className="flex items-start gap-2.5">
            <span
              className="mt-0.5 flex h-5.5 w-5.5 min-h-[1.375rem] min-w-[1.375rem] shrink-0 items-center justify-center rounded-full font-display text-[0.66rem] font-bold"
              style={{ background: "var(--primary)", color: "var(--bg-page)" }}
            >
              {i + 1}
            </span>
            <span className="text-[0.74rem] leading-relaxed" style={{ color: "var(--text)" }}>
              {s}
            </span>
          </li>
        ))}
      </ol>

      <button
        type="button"
        onClick={onDownload}
        disabled={zipping}
        className="inline-flex items-center justify-center gap-2 self-start rounded-[var(--radius-sm)] border px-4 py-2 font-mono text-[0.74rem] font-bold transition-all duration-200 enabled:hover:-translate-y-px disabled:opacity-70"
        style={{
          background: "var(--primary)",
          borderColor: "var(--primary)",
          color: "var(--bg-page)",
          boxShadow: "0 6px 18px -8px color-mix(in oklab, var(--primary) 70%, transparent)",
        }}
      >
        <DownloadIcon />
        {zipping ? "gerando…" : "Baixar temas-crv-patch-win.zip"}
      </button>

      <p className="text-[0.66rem] leading-relaxed" style={{ color: "var(--muted)" }}>
        O <strong style={{ color: "var(--text)" }}>.bat</strong> copia os 4 arquivos novos, roda as edições protegidas
        (via <code className="font-mono" style={{ color: "var(--primary)" }}>editar.py</code>), gera a migration e —
        com sua confirmação — commita e faz o push. Se o Windows bloquear, clique em{" "}
        <em>"Mais informações" → "Executar assim mesmo"</em>. Se o caminho acima mudar, ele pede para você arrastar a
        pasta do repositório para a janela.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Seção                                                               */
/* ------------------------------------------------------------------ */

export function PatchSection() {
  const files = useMemo(() => buildPatchFiles(), []);
  const [active, setActive] = useState(0);
  const [copied, copy] = useCopy();
  const [zipping, setZipping] = useState(false);
  const [mode, setMode] = useState<"win" | "terminal" | "browser">("win");
  const file = files[active];
  const lines = useMemo(() => file.content.split("\n").length, [file]);

  const downloadZip = async () => {
    setZipping(true);
    try {
      if (mode === "win") {
        await downloadWindowsPatchZip();
        reportFramedDownload("temas-crv-patch-win.zip", () => void downloadWindowsPatchZip());
      } else {
        await downloadPatchZip();
        reportFramedDownload("temas-crv-patch.zip", () => void downloadPatchZip());
      }
    } finally {
      setTimeout(() => setZipping(false), 600);
    }
  };

  return (
    <div>
      {/* meta bar — procedência + zip */}
      <Reveal>
        <div
          className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[var(--radius)] border px-4 py-3"
          style={{ background: "var(--card)", borderColor: "var(--border)" }}
        >
          <a
            href="https://github.com/MSPA-Coder/ControleRendaVariavel"
            target="_blank"
            rel="noreferrer"
            className="group inline-flex items-center gap-2 font-mono text-[0.78rem] font-semibold"
            style={{ color: "var(--text)" }}
          >
            <GitHubIcon className="text-[1.05rem]" />
            MSPA-Coder/ControleRendaVariavel
            <span className="rounded border px-1.5 py-px text-[0.62rem]" style={{ borderColor: "var(--border)", color: "var(--muted)" }}>
              main
            </span>
            <ArrowUpRightIcon className="text-[0.7rem] opacity-50 transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
          </a>
          <span className="hidden h-4 w-px sm:block" style={{ background: "var(--border)" }} />
          <span className="inline-flex items-center gap-1.5 font-mono text-[0.68rem]" style={{ color: "var(--muted)" }}>
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--success)" }} />
            app.css + models.py lidos do GitHub
          </span>
          <button
            type="button"
            onClick={downloadZip}
            disabled={zipping}
            className="ml-auto inline-flex items-center gap-2 rounded-[var(--radius-sm)] border px-3.5 py-1.5 font-mono text-[0.72rem] font-bold transition-all duration-200 enabled:hover:-translate-y-px disabled:opacity-70"
            style={{
              background: "var(--primary)",
              borderColor: "var(--primary)",
              color: "var(--bg-page)",
              boxShadow: "0 6px 18px -8px color-mix(in oklab, var(--primary) 70%, transparent)",
            }}
          >
            <DownloadIcon />
            {zipping ? "gerando…" : mode === "win" ? "Baixar ZIP Windows (.bat)" : "Baixar tudo (.zip)"}
          </button>
        </div>
      </Reveal>

      {/* como aplicar: terminal ou navegador + simulador */}
      <div className="mb-4 grid gap-3 lg:grid-cols-[1fr_1.1fr]">
        <Reveal delay={60}>
          <div
            className="flex h-full flex-col gap-4 rounded-[var(--radius-lg)] border p-4 sm:p-5"
            style={{ background: "var(--card)", borderColor: "var(--border)" }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-[0.95rem] font-bold" style={{ color: "var(--text)" }}>
                  Como aplicar
                </h3>
                <p className="mt-0.5 text-[0.72rem] leading-relaxed" style={{ color: "var(--muted)" }}>
                  No Windows com um .bat, com 3 comandos no terminal ou direto pelo navegador.
                </p>
              </div>
              {/* seletor de modo */}
              <div
                className="flex rounded-[var(--radius)] border p-0.5"
                style={{ borderColor: "var(--border)", background: "var(--bg-page)" }}
                role="tablist"
                aria-label="Modo de aplicação do patch"
              >
                {(
                  [
                    { id: "win", label: "Windows", icon: <DownloadIcon className="text-[0.85rem]" /> },
                    { id: "terminal", label: "Terminal", icon: <TerminalIcon className="text-[0.85rem]" /> },
                    { id: "browser", label: "Navegador", icon: <BrowserIcon className="text-[0.85rem]" /> },
                  ] as const
                ).map((m) => {
                  const on = mode === m.id;
                  return (
                    <button
                      key={m.id}
                      type="button"
                      role="tab"
                      aria-selected={on}
                      onClick={() => setMode(m.id)}
                      className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] px-3 py-1.5 font-mono text-[0.68rem] font-bold uppercase tracking-wider transition-all duration-200"
                      style={
                        on
                          ? { background: "var(--primary)", color: "var(--bg-page)", boxShadow: "0 4px 12px -6px color-mix(in oklab, var(--primary) 70%, transparent)" }
                          : { color: "var(--muted)" }
                      }
                    >
                      {m.icon}
                      {m.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {mode === "terminal" ? (
              <div className="flex flex-col gap-3.5">
                {QUICK_STEPS.map((s) => (
                  <QuickStep key={s.n} step={s} />
                ))}
                <p className="text-[0.66rem] leading-relaxed" style={{ color: "var(--muted)" }}>
                  O zip já traz os arquivos nos caminhos certos e o{" "}
                  <code className="font-mono" style={{ color: "var(--primary)" }}>aplicar_patch.sh</code>: ele cria,
                  edita, migra e — <strong style={{ color: "var(--text)" }}>só com o seu "s"</strong> — commita e faz o
                  push.
                </p>
              </div>
            ) : mode === "win" ? (
              <WindowsMode onDownload={downloadZip} zipping={zipping} />
            ) : (
              <BrowserMode files={files} onOpen={setActive} />
            )}
          </div>
        </Reveal>
        <Reveal delay={120}>
          <InstallSimulator />
        </Reveal>
      </div>

      <Reveal delay={80}>
        <div className="grid gap-3 lg:grid-cols-[280px_1fr]">
          {/* lista de arquivos */}
          <nav aria-label="Arquivos do patch" className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
            {files.map((f, i) => {
              const on = i === active;
              const ks = KIND_STYLE[f.kind];
              return (
                <button
                  key={f.path}
                  type="button"
                  onClick={() => setActive(i)}
                  className="shrink-0 rounded-[var(--radius)] border px-3 py-2.5 text-left transition-all duration-200 lg:shrink"
                  style={{
                    minWidth: "190px",
                    background: on ? "var(--primary-light)" : "var(--card)",
                    borderColor: on ? "var(--primary)" : "var(--border)",
                  }}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span
                      className="truncate font-mono text-[0.72rem] font-semibold"
                      style={{ color: on ? "var(--primary)" : "var(--text)" }}
                    >
                      {f.path}
                    </span>
                    <span
                      className="rounded px-1.5 py-px font-mono text-[0.56rem] font-bold tracking-wider"
                      style={{
                        color: ks.color,
                        background: `color-mix(in oklab, ${ks.color} 13%, transparent)`,
                      }}
                    >
                      {ks.label}
                    </span>
                  </span>
                  <span className="mt-0.5 hidden text-[0.66rem] leading-snug lg:block" style={{ color: "var(--muted)" }}>
                    {f.desc}
                  </span>
                </button>
              );
            })}
          </nav>

          {/* viewer */}
          <div
            className="flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-lg)] border"
            style={{ background: "var(--card)", borderColor: "var(--border)" }}
          >
            <div
              className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5"
              style={{ background: "var(--bg-header)", borderColor: "var(--border)" }}
            >
              <span className="font-mono text-[0.74rem] font-bold" style={{ color: "var(--text)" }}>
                {file.path}
              </span>
              <span className="font-mono text-[0.64rem]" style={{ color: "var(--muted)" }}>
                {lines} linhas
              </span>
              <span className="ml-auto flex gap-1.5">
                <button
                  type="button"
                  onClick={() => copy(file.content)}
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.66rem] font-semibold transition-all duration-150 hover:-translate-y-px"
                  style={{
                    borderColor: "var(--border)",
                    background: "var(--card)",
                    color: copied ? "var(--success)" : "var(--muted)",
                  }}
                >
                  {copied ? <CheckIcon /> : <CopyIcon />}
                  {copied ? "copiado" : "copiar"}
                </button>
                <button
                  type="button"
                  onClick={() => saveText(file.path, file.content)}
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-2.5 py-1 font-mono text-[0.66rem] font-semibold transition-all duration-150 hover:-translate-y-px"
                  style={{ borderColor: "var(--border)", background: "var(--card)", color: "var(--muted)" }}
                >
                  <DownloadIcon />
                  baixar
                </button>
              </span>
            </div>
            <pre className="code-block max-h-[520px] flex-1 overflow-auto rounded-none border-0">
              <code>{file.content}</code>
            </pre>
            <div
              className="border-t px-4 py-2 font-mono text-[0.64rem]"
              style={{ borderColor: "var(--border)", color: "var(--muted)" }}
            >
              {file.desc}
            </div>
          </div>
        </div>
      </Reveal>

      <Reveal delay={140}>
        <p className="mt-3 text-[0.74rem] leading-relaxed" style={{ color: "var(--muted)" }}>
          O instalador segue a ordem <strong style={{ color: "var(--text)" }}>themes.py → models.py → routes →
          templates → theme_system.css → migration</strong>. Como o CSS redefine apenas as variáveis que o app já usa
          (<code className="font-mono" style={{ color: "var(--primary)" }}>--bg</code>,{" "}
          <code className="font-mono" style={{ color: "var(--primary)" }}>--surface</code>,{" "}
          <code className="font-mono" style={{ color: "var(--primary)" }}>--accent</code>…), todos os 13 temas —
          inclusive o <strong style={{ color: "var(--text)" }}>Original RV</strong>, que replica o visual de hoje —
          funcionam sem mexer em nenhuma regra existente.
        </p>
      </Reveal>
    </div>
  );
}
