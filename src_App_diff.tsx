--- src/App.tsx (原始)
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ALL_THEMES, buildThemeCss, DEFAULT_THEME, THEME_MAP } from "./data/themes";
import { downloadWindowsPatchZip } from "./lib/download";
import { DownloadIcon } from "./components/icons";
import { TickerTape } from "./components/TickerTape";
import { ThemeGrid } from "./components/ThemeGrid";
import { DashboardPreview } from "./components/DashboardPreview";
import { TokenInspector } from "./components/TokenInspector";
import { IntegrationGuide } from "./components/IntegrationGuide";
import { PatchSection } from "./components/PatchSection";
import { Reveal } from "./components/Reveal";
import { ArrowUpRightIcon, CandlesIcon, CheckIcon, GitHubIcon } from "./components/icons";

const STORAGE_KEY = "crv-ui-theme";

function SectionHead({
  num,
  kicker,
  title,
  desc,
  side,
}: {
  num: string;
  kicker: string;
  title: string;
  desc?: string;
  side?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
      <div className="max-w-2xl">
        <p className="mb-2 flex items-center gap-2.5 font-mono text-[0.68rem] font-medium uppercase tracking-[0.22em]" style={{ color: "var(--primary)" }}>
          <span className="inline-block h-[3px] w-7 rounded-full" style={{ background: "var(--primary)" }} />
          {num} · {kicker}
        </p>
        <h2 className="font-display text-3xl font-bold tracking-tight sm:text-4xl" style={{ color: "var(--text)" }}>
          {title}
        </h2>
        {desc && (
          <p className="mt-2.5 text-[0.9rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            {desc}
          </p>
        )}
      </div>
      {side}
    </div>
  );
}

function FloatingZip() {
  const [shown, setShown] = useState(false);
  const [zipping, setZipping] = useState(false);

  useEffect(() => {
    const onScroll = () => setShown(window.scrollY > 280);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const handle = async () => {
    setZipping(true);
    try {
      await downloadWindowsPatchZip();
    } finally {
      setTimeout(() => setZipping(false), 600);
    }
  };

  return (
    <button
      type="button"
      onClick={handle}
      disabled={zipping}
      className={`fixed bottom-5 left-5 z-50 inline-flex items-center gap-2.5 rounded-full border px-4 py-2.5 font-mono text-[0.72rem] font-bold shadow-[0_18px_44px_-16px_rgba(0,0,0,0.55)] transition-all duration-500 enabled:hover:-translate-y-0.5 disabled:opacity-80 ${
        shown ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-16 opacity-0"
      }`}
      style={{
        background: "var(--primary)",
        borderColor: "color-mix(in oklab, var(--primary) 60%, var(--text))",
        color: "var(--bg-page)",
      }}
      title="Baixar o patch completo (.zip)"
    >
      <DownloadIcon className="text-[0.95rem]" />
      {zipping ? "gerando zip…" : "temas-crv-patch-win.zip"}
      <span
        className="rounded-full px-1.5 py-px text-[0.58rem] uppercase tracking-wider"
        style={{ background: "color-mix(in oklab, var(--bg-page) 22%, transparent)" }}
      >
        Windows · .bat
      </span>
    </button>
  );
}

function LiveTerminal({ themeId }: { themeId: string }) {
  return (
    <div
      className="overflow-hidden rounded-[var(--radius-lg)] border shadow-[0_24px_60px_-30px_rgba(0,0,0,0.5)]"
      style={{ background: "var(--bg-header)", borderColor: "var(--border)" }}
    >
      <div className="flex items-center gap-3 border-b px-4 py-2.5" style={{ borderColor: "var(--border)" }}>
        <span className="flex gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--danger)", opacity: 0.85 }} />
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--warning)", opacity: 0.85 }} />
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--success)", opacity: 0.85 }} />
        </span>
        <span className="font-mono text-[0.68rem]" style={{ color: "var(--muted)" }}>
          base.html — Jinja render
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5">
          <span className="live-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--success)" }} />
          <span className="font-mono text-[0.62rem] uppercase tracking-widest" style={{ color: "var(--muted)" }}>
            ao vivo
          </span>
        </span>
      </div>
      <div className="p-4 font-mono text-[0.72rem] leading-[1.9] sm:p-5 sm:text-[0.76rem]">
        <p style={{ color: "var(--muted)" }}>
          <span style={{ color: "var(--success)" }}>$</span> flask run{" "}
          <span style={{ color: "var(--muted)" }}>· GET /carteira</span>
        </p>
        <p style={{ color: "var(--text)" }}>
          &lt;html lang=<span style={{ color: "var(--success)" }}>"pt-BR"</span>
        </p>
        <p className="pl-5" style={{ color: "var(--text)" }}>
          data-theme=<span style={{ color: "var(--primary)", fontWeight: 600 }}>"{themeId}"</span>&gt;
        </p>
        <p style={{ color: "var(--muted)" }}>
          {"  "}
          <span style={{ color: "var(--warning)" }}>current_user</span>.ui_theme{" "}
          <span style={{ color: "var(--text)" }}>→</span>{" "}
          <span style={{ color: "var(--success)" }}>"{themeId}"</span>
          <span className="caret ml-1 inline-block h-[1.05em] w-[0.55em] translate-y-[0.22em]" style={{ background: "var(--primary)" }} />
        </p>
        <p className="mt-3 border-t pt-3 text-[0.66rem]" style={{ borderColor: "var(--border)", color: "var(--muted)" }}>
          ↑ troque o tema na biblioteca e observe o atributo mudar — sem recarregar a página.
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const [themeId, setThemeId] = useState<string>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return saved && THEME_MAP[saved] ? saved : DEFAULT_THEME;
    } catch {
      return DEFAULT_THEME;
    }
  });
  const [toast, setToast] = useState<{ msg: string; key: number } | null>(null);
  const firstRender = useRef(true);
  const toastTimer = useRef<number | undefined>(undefined);
  const themeCss = useMemo(() => buildThemeCss(ALL_THEMES), []);
  const theme = THEME_MAP[themeId];

  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("theming");
    root.dataset.theme = themeId;
    try {
      localStorage.setItem(STORAGE_KEY, themeId);
    } catch {
      /* armazenamento indisponível */
    }
    const cleanup = window.setTimeout(() => root.classList.remove("theming"), 520);

    if (firstRender.current) {
      firstRender.current = false;
    } else {
      window.clearTimeout(toastTimer.current);
      setToast({ msg: `Tema alterado para: ${theme.label}.`, key: Date.now() });
      toastTimer.current = window.setTimeout(() => setToast(null), 2800);
    }
    return () => {
      window.clearTimeout(cleanup);
      window.clearTimeout(toastTimer.current);
    };
  }, [themeId, theme.label]);

  return (
    <div className="min-h-screen font-body">
      <style>{themeCss}</style>

      {/* fita de cotações — pausa no hover */}
      <TickerTape />

      {/* download do patch, sempre à mão */}
      <FloatingZip />

      {/* topo fixo */}
      <header
        className="sticky top-0 z-40 border-b backdrop-blur-md"
        style={{
          background: "color-mix(in oklab, var(--bg-header) 84%, transparent)",
          borderColor: "var(--border)",
        }}
      >
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3 sm:px-6">
          <a href="#topo" className="flex items-center gap-2.5">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-[var(--radius)] text-[1.25rem]"
              style={{ background: "var(--primary)", color: theme.dark ? "#10131a" : "#ffffff" }}
            >
              <CandlesIcon />
            </span>
            <span className="leading-tight">
              <span className="block font-display text-[0.95rem] font-bold tracking-tight" style={{ color: "var(--text)" }}>
                CRV · Central de Temas
              </span>
              <span className="block font-mono text-[0.6rem] uppercase tracking-[0.18em]" style={{ color: "var(--muted)" }}>
                ControleRendaVariável
              </span>
            </span>
          </a>

          <nav className="ml-auto hidden items-center gap-5 text-[0.78rem] font-medium md:flex" style={{ color: "var(--muted)" }}>
            <a href="#biblioteca" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Biblioteca
            </a>
            <a href="#previa" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Prévia
            </a>
            <a href="#tokens" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Tokens
            </a>
            <a href="#patch" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Patch
            </a>
            <a href="#integracao" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Integração
            </a>
          </nav>

          <div className="ml-auto flex items-center gap-2.5 md:ml-5">
            <span
              className="hidden items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-[0.68rem] sm:inline-flex"
              style={{ borderColor: "var(--border)", background: "var(--card)", color: "var(--text)" }}
            >
              <span className="h-2 w-2 rounded-full" style={{ background: "var(--primary)" }} />
              data-theme="<strong style={{ color: "var(--primary)" }}>{themeId}</strong>"
            </span>
            <a
              href="https://github.com/mspa-coder"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[0.72rem] font-semibold transition-all duration-150 hover:-translate-y-px"
              style={{
                borderColor: "var(--primary)",
                background: "var(--primary-light)",
                color: "var(--primary)",
              }}
            >
              <GitHubIcon className="text-[0.9rem]" />
              mspa-coder
            </a>
          </div>
        </div>
      </header>

      <main id="topo" className="mx-auto max-w-6xl px-4 sm:px-6">
        {/* abertura */}
        <section className="grid items-center gap-10 py-12 sm:py-16 lg:grid-cols-[1.15fr_0.85fr] lg:py-20">
          <Reveal>
            <p className="mb-4 font-mono text-[0.7rem] font-medium uppercase tracking-[0.24em]" style={{ color: "var(--primary)" }}>
              {"/* sistema de aparência · do sistema-financeiro para o CRV */"}
            </p>
            <h1
              className="font-display text-[2.7rem] font-bold leading-[0.99] tracking-tight sm:text-6xl xl:text-[4.6rem]"
              style={{ color: "var(--text)" }}
            >
              Treze temas.
              <br />
              Um atributo no{" "}
              <span className="font-mono text-[0.72em] font-semibold" style={{ color: "var(--primary)" }}>
                &lt;html&gt;
              </span>
              .
            </h1>
            <p className="mt-6 max-w-xl text-[0.95rem] leading-relaxed sm:text-base" style={{ color: "var(--muted)" }}>
              Esta página é a demonstração viva do sistema de temas da conversa: os mesmos{" "}
              <strong style={{ color: "var(--text)" }}>12 temas</strong> do{" "}
              <code className="font-mono text-[0.85em]" style={{ color: "var(--primary)" }}>
                sistema-financeiro
              </code>{" "}
              mais o <strong style={{ color: "var(--text)" }}>Original RV</strong> — o tema atual do app, extraído do{" "}
              <code className="font-mono text-[0.85em]" style={{ color: "var(--primary)" }}>
                app.css
              </code>{" "}
              real do{" "}
              <code className="font-mono text-[0.85em]" style={{ color: "var(--primary)" }}>
                ControleRendaVariável
              </code>
              . Escolha um tema abaixo — a página inteira responde na hora, do jeito que o{" "}
              <code className="font-mono text-[0.85em]" style={{ color: "var(--primary)" }}>
                data-theme
              </code>{" "}
              foi projetado para funcionar.
            </p>

            <div className="mt-8 flex flex-wrap gap-2.5">
              {[
                ["13", "temas prontos"],
                ["11", "tokens CSS"],
                ["1", "coluna ui_theme"],
                ["0", "JS no load"],
              ].map(([n, label]) => (
                <span
                  key={label}
                  className="inline-flex items-baseline gap-2 rounded-full border px-4 py-2"
                  style={{ borderColor: "var(--border)", background: "var(--card)" }}
                >
                  <span className="font-display text-lg font-bold leading-none" style={{ color: "var(--primary)" }}>
                    {n}
                  </span>
                  <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em]" style={{ color: "var(--muted)" }}>
                    {label}
                  </span>
                </span>
              ))}
            </div>
          </Reveal>

          <Reveal delay={140}>
            <LiveTerminal themeId={themeId} />
          </Reveal>
        </section>

        {/* 01 · biblioteca */}
        <section id="biblioteca" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="01"
              kicker="biblioteca de temas"
              title="Escolha o tema — a página obedece"
              desc="O mesmo grid de seleção do profile.css: miniatura, nome e descrição. Aqui o clique aplica o tema instantaneamente e salva sua preferência, como a rota /profile/theme faria no Flask."
            />
          </Reveal>
          <Reveal delay={100}>
            <ThemeGrid active={themeId} onSelect={setThemeId} />
          </Reveal>
        </section>

        {/* 02 · prévia */}
        <section id="previa" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="02"
              kicker="prévia ao vivo"
              title="A carteira vestindo o tema"
              desc="Uma maquete funcional da tela principal do ControleRendaVariável: resumo do patrimônio, evolução da carteira e posições em ações, ETFs e opções. Tudo pintado pelos tokens do tema ativo."
              side={
                <span
                  className="hidden rounded-full border px-3.5 py-1.5 font-mono text-[0.66rem] uppercase tracking-[0.16em] lg:inline-block"
                  style={{ borderColor: "var(--border)", color: "var(--muted)", background: "var(--card)" }}
                >
                  tema ativo: {theme.label}
                </span>
              }
            />
          </Reveal>
          <Reveal delay={100}>
            <DashboardPreview />
          </Reveal>
        </section>

        {/* 03 · tokens */}
        <section id="tokens" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="03"
              kicker="inspetor de tokens"
              title="O que o tema injeta agora"
              desc="Cada tema é um bloco html[data-theme=...] com estes pares de variáveis. Inspecione os valores do tema ativo e copie qualquer um com um clique."
            />
          </Reveal>
          <Reveal delay={100}>
            <TokenInspector theme={theme} />
          </Reveal>
        </section>

        {/* 04 · patch pronto */}
        <section id="patch" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="04"
              kicker="patch pronto · lido do github"
              title="Os 4 passos, ajustados ao Projeto 4 real"
              desc="Li o app/static/app.css e o app/models.py direto do repositório. O patch fala as variáveis que o app já usa (--bg, --surface, --accent…) e traz o tema atual como o 13º — Original RV. No Windows, baixe o ZIP pelo botão flutuante: o aplicar_patch.bat instala direto em …\\VSCodeProjects\\ControleRendaVariavel com dois cliques."
            />
          </Reveal>
          <PatchSection />
        </section>

        {/* 05 · integração */}
        <section id="integracao" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="05"
              kicker="integração no flask · como funciona"
              title="O mecanismo, em quatro passos"
              desc="O roteiro que saiu da conversa para levar o sistema ao ControleRendaVariável: banco, rota, template base e CSS. A seção anterior tem os arquivos completos; aqui, a lógica de cada passo."
            />
          </Reveal>
          <IntegrationGuide />
        </section>
      </main>

      {/* rodapé */}
      <footer className="mt-8 border-t" style={{ borderColor: "var(--border)", background: "var(--bg-header)" }}>
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div className="flex items-center gap-2.5">
            <span
              className="flex h-7 w-7 items-center justify-center rounded-md text-[1rem]"
              style={{ background: "var(--primary-light)", color: "var(--primary)" }}
            >
              <CandlesIcon />
            </span>
            <p className="font-mono text-[0.68rem]" style={{ color: "var(--muted)" }}>
              Central de Temas · demo interativa do ControleRendaVariável
            </p>
          </div>
          <p className="max-w-md text-[0.72rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            Conteúdo reproduzido da conversa no Qwen Chat sobre os projetos de{" "}
            <a
              href="https://github.com/mspa-coder"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 font-semibold transition-opacity hover:opacity-70"
              style={{ color: "var(--primary)" }}
            >
              github.com/mspa-coder <ArrowUpRightIcon className="text-[0.7rem]" />
            </a>{" "}
            — Flask, SQLAlchemy, Alembic e HTMX. Dados de mercado simulados.
          </p>
        </div>
      </footer>

      {/* flash de tema — espelha o flash() do Flask */}
      {toast && (
        <div
          key={toast.key}
          className="toast-in fixed bottom-5 right-5 z-50 flex items-center gap-3 rounded-[var(--radius)] border px-4 py-3 shadow-[0_18px_44px_-16px_rgba(0,0,0,0.5)]"
          role="status"
          style={{ background: "var(--card)", borderColor: "var(--border)", borderLeft: "3px solid var(--success)" }}
        >
          <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[0.8rem]"
            style={{ background: "var(--success)", color: theme.dark ? "#10131a" : "#ffffff" }}
          >
            <CheckIcon />
          </span>
          <span>
            <span className="block text-[0.82rem] font-semibold" style={{ color: "var(--text)" }}>
              {toast.msg}
            </span>
            <span className="block font-mono text-[0.64rem]" style={{ color: "var(--muted)" }}>
              preferência salva · localStorage["{STORAGE_KEY}"]
            </span>
          </span>
        </div>
      )}
    </div>
  );
}


+++ src/App.tsx (修改后)
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ALL_THEMES, buildThemeCss, DEFAULT_THEME, THEME_MAP } from "./data/themes";
import { downloadWindowsPatchZip } from "./lib/download";
import { reportFramedDownload, setGateListener, type GatePayload } from "./lib/downloadGate";
import { DownloadAssist } from "./components/DownloadAssist";
import { DownloadIcon } from "./components/icons";
import { TickerTape } from "./components/TickerTape";
import { ThemeGrid } from "./components/ThemeGrid";
import { DashboardPreview } from "./components/DashboardPreview";
import { TokenInspector } from "./components/TokenInspector";
import { IntegrationGuide } from "./components/IntegrationGuide";
import { PatchSection } from "./components/PatchSection";
import { Reveal } from "./components/Reveal";
import { ArrowUpRightIcon, CandlesIcon, CheckIcon, GitHubIcon } from "./components/icons";

const STORAGE_KEY = "crv-ui-theme";

function SectionHead({
  num,
  kicker,
  title,
  desc,
  side,
}: {
  num: string;
  kicker: string;
  title: string;
  desc?: string;
  side?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
      <div className="max-w-2xl">
        <p className="mb-2 flex items-center gap-2.5 font-mono text-[0.68rem] font-medium uppercase tracking-[0.22em]" style={{ color: "var(--primary)" }}>
          <span className="inline-block h-[3px] w-7 rounded-full" style={{ background: "var(--primary)" }} />
          {num} · {kicker}
        </p>
        <h2 className="font-display text-3xl font-bold tracking-tight sm:text-4xl" style={{ color: "var(--text)" }}>
          {title}
        </h2>
        {desc && (
          <p className="mt-2.5 text-[0.9rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            {desc}
          </p>
        )}
      </div>
      {side}
    </div>
  );
}

function FloatingZip() {
  const [shown, setShown] = useState(false);
  const [zipping, setZipping] = useState(false);

  useEffect(() => {
    const onScroll = () => setShown(window.scrollY > 280);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const handle = async () => {
    setZipping(true);
    try {
      await downloadWindowsPatchZip();
      reportFramedDownload("temas-crv-patch-win.zip", () => {
        void downloadWindowsPatchZip();
      });
    } finally {
      setTimeout(() => setZipping(false), 600);
    }
  };

  return (
    <button
      type="button"
      onClick={handle}
      disabled={zipping}
      className={`fixed bottom-5 left-5 z-50 inline-flex items-center gap-2.5 rounded-full border px-4 py-2.5 font-mono text-[0.72rem] font-bold shadow-[0_18px_44px_-16px_rgba(0,0,0,0.55)] transition-all duration-500 enabled:hover:-translate-y-0.5 disabled:opacity-80 ${
        shown ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-16 opacity-0"
      }`}
      style={{
        background: "var(--primary)",
        borderColor: "color-mix(in oklab, var(--primary) 60%, var(--text))",
        color: "var(--bg-page)",
      }}
      title="Baixar o patch completo (.zip)"
    >
      <DownloadIcon className="text-[0.95rem]" />
      {zipping ? "gerando zip…" : "temas-crv-patch-win.zip"}
      <span
        className="rounded-full px-1.5 py-px text-[0.58rem] uppercase tracking-wider"
        style={{ background: "color-mix(in oklab, var(--bg-page) 22%, transparent)" }}
      >
        Windows · .bat
      </span>
    </button>
  );
}

function LiveTerminal({ themeId }: { themeId: string }) {
  return (
    <div
      className="overflow-hidden rounded-[var(--radius-lg)] border shadow-[0_24px_60px_-30px_rgba(0,0,0,0.5)]"
      style={{ background: "var(--bg-header)", borderColor: "var(--border)" }}
    >
      <div className="flex items-center gap-3 border-b px-4 py-2.5" style={{ borderColor: "var(--border)" }}>
        <span className="flex gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--danger)", opacity: 0.85 }} />
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--warning)", opacity: 0.85 }} />
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--success)", opacity: 0.85 }} />
        </span>
        <span className="font-mono text-[0.68rem]" style={{ color: "var(--muted)" }}>
          base.html — Jinja render
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5">
          <span className="live-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--success)" }} />
          <span className="font-mono text-[0.62rem] uppercase tracking-widest" style={{ color: "var(--muted)" }}>
            ao vivo
          </span>
        </span>
      </div>
      <div className="p-4 font-mono text-[0.72rem] leading-[1.9] sm:p-5 sm:text-[0.76rem]">
        <p style={{ color: "var(--muted)" }}>
          <span style={{ color: "var(--success)" }}>$</span> flask run{" "}
          <span style={{ color: "var(--muted)" }}>· GET /carteira</span>
        </p>
        <p style={{ color: "var(--text)" }}>
          &lt;html lang=<span style={{ color: "var(--success)" }}>"pt-BR"</span>
        </p>
        <p className="pl-5" style={{ color: "var(--text)" }}>
          data-theme=<span style={{ color: "var(--primary)", fontWeight: 600 }}>"{themeId}"</span>&gt;
        </p>
        <p style={{ color: "var(--muted)" }}>
          {"  "}
          <span style={{ color: "var(--warning)" }}>current_user</span>.ui_theme{" "}
          <span style={{ color: "var(--text)" }}>→</span>{" "}
          <span style={{ color: "var(--success)" }}>"{themeId}"</span>
          <span className="caret ml-1 inline-block h-[1.05em] w-[0.55em] translate-y-[0.22em]" style={{ background: "var(--primary)" }} />
        </p>
        <p className="mt-3 border-t pt-3 text-[0.66rem]" style={{ borderColor: "var(--border)", color: "var(--muted)" }}>
          ↑ troque o tema na biblioteca e observe o atributo mudar — sem recarregar a página.
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const [themeId, setThemeId] = useState<string>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return saved && THEME_MAP[saved] ? saved : DEFAULT_THEME;
    } catch {
      return DEFAULT_THEME;
    }
  });
  const [toast, setToast] = useState<{ msg: string; key: number } | null>(null);
  const firstRender = useRef(true);
  const toastTimer = useRef<number | undefined>(undefined);
  const themeCss = useMemo(() => buildThemeCss(ALL_THEMES), []);
  const theme = THEME_MAP[themeId];

  // assistente de download (a página roda dentro do preview, que bloqueia downloads)
  const [gate, setGate] = useState<GatePayload | null>(null);
  useEffect(() => {
    setGateListener(setGate);
    return () => setGateListener(null);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("theming");
    root.dataset.theme = themeId;
    try {
      localStorage.setItem(STORAGE_KEY, themeId);
    } catch {
      /* armazenamento indisponível */
    }
    const cleanup = window.setTimeout(() => root.classList.remove("theming"), 520);

    if (firstRender.current) {
      firstRender.current = false;
    } else {
      window.clearTimeout(toastTimer.current);
      setToast({ msg: `Tema alterado para: ${theme.label}.`, key: Date.now() });
      toastTimer.current = window.setTimeout(() => setToast(null), 2800);
    }
    return () => {
      window.clearTimeout(cleanup);
      window.clearTimeout(toastTimer.current);
    };
  }, [themeId, theme.label]);

  return (
    <div className="min-h-screen font-body">
      <style>{themeCss}</style>

      {/* fita de cotações — pausa no hover */}
      <TickerTape />

      {/* download do patch, sempre à mão */}
      <FloatingZip />

      {/* topo fixo */}
      <header
        className="sticky top-0 z-40 border-b backdrop-blur-md"
        style={{
          background: "color-mix(in oklab, var(--bg-header) 84%, transparent)",
          borderColor: "var(--border)",
        }}
      >
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3 sm:px-6">
          <a href="#topo" className="flex items-center gap-2.5">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-[var(--radius)] text-[1.25rem]"
              style={{ background: "var(--primary)", color: theme.dark ? "#10131a" : "#ffffff" }}
            >
              <CandlesIcon />
            </span>
            <span className="leading-tight">
              <span className="block font-display text-[0.95rem] font-bold tracking-tight" style={{ color: "var(--text)" }}>
                CRV · Central de Temas
              </span>
              <span className="block font-mono text-[0.6rem] uppercase tracking-[0.18em]" style={{ color: "var(--muted)" }}>
                ControleRendaVariável
              </span>
            </span>
          </a>

          <nav className="ml-auto hidden items-center gap-5 text-[0.78rem] font-medium md:flex" style={{ color: "var(--muted)" }}>
            <a href="#biblioteca" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Biblioteca
            </a>
            <a href="#previa" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Prévia
            </a>
            <a href="#tokens" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Tokens
            </a>
            <a href="#patch" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Patch
            </a>
            <a href="#integracao" className="transition-colors hover:opacity-75" style={{ color: "inherit" }}>
              Integração
            </a>
          </nav>

          <div className="ml-auto flex items-center gap-2.5 md:ml-5">
            <span
              className="hidden items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-[0.68rem] sm:inline-flex"
              style={{ borderColor: "var(--border)", background: "var(--card)", color: "var(--text)" }}
            >
              <span className="h-2 w-2 rounded-full" style={{ background: "var(--primary)" }} />
              data-theme="<strong style={{ color: "var(--primary)" }}>{themeId}</strong>"
            </span>
            <a
              href="https://github.com/mspa-coder"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[0.72rem] font-semibold transition-all duration-150 hover:-translate-y-px"
              style={{
                borderColor: "var(--primary)",
                background: "var(--primary-light)",
                color: "var(--primary)",
              }}
            >
              <GitHubIcon className="text-[0.9rem]" />
              mspa-coder
            </a>
          </div>
        </div>
      </header>

      <main id="topo" className="mx-auto max-w-6xl px-4 sm:px-6">
        {/* abertura */}
        <section className="grid items-center gap-10 py-12 sm:py-16 lg:grid-cols-[1.15fr_0.85fr] lg:py-20">
          <Reveal>
            <p className="mb-4 font-mono text-[0.7rem] font-medium uppercase tracking-[0.24em]" style={{ color: "var(--primary)" }}>
              {"/* sistema de aparência · do sistema-financeiro para o CRV */"}
            </p>
            <h1
              className="font-display text-[2.7rem] font-bold leading-[0.99] tracking-tight sm:text-6xl xl:text-[4.6rem]"
              style={{ color: "var(--text)" }}
            >
              Treze temas.
              <br />
              Um atributo no{" "}
              <span className="font-mono text-[0.72em] font-semibold" style={{ color: "var(--primary)" }}>
                &lt;html&gt;
              </span>
              .
            </h1>
            <p className="mt-6 max-w-xl text-[0.95rem] leading-relaxed sm:text-base" style={{ color: "var(--muted)" }}>
              Esta página é a demonstração viva do sistema de temas da conversa: os mesmos{" "}
              <strong style={{ color: "var(--text)" }}>12 temas</strong> do{" "}
              <code className="font-mono text-[0.85em]" style={{ color: "var(--primary)" }}>
                sistema-financeiro
              </code>{" "}
              mais o <strong style={{ color: "var(--text)" }}>Original RV</strong> — o tema atual do app, extraído do{" "}
              <code className="font-mono text-[0.85em]" style={{ color: "var(--primary)" }}>
                app.css
              </code>{" "}
              real do{" "}
              <code className="font-mono text-[0.85em]" style={{ color: "var(--primary)" }}>
                ControleRendaVariável
              </code>
              . Escolha um tema abaixo — a página inteira responde na hora, do jeito que o{" "}
              <code className="font-mono text-[0.85em]" style={{ color: "var(--primary)" }}>
                data-theme
              </code>{" "}
              foi projetado para funcionar.
            </p>

            <div className="mt-8 flex flex-wrap gap-2.5">
              {[
                ["13", "temas prontos"],
                ["11", "tokens CSS"],
                ["1", "coluna ui_theme"],
                ["0", "JS no load"],
              ].map(([n, label]) => (
                <span
                  key={label}
                  className="inline-flex items-baseline gap-2 rounded-full border px-4 py-2"
                  style={{ borderColor: "var(--border)", background: "var(--card)" }}
                >
                  <span className="font-display text-lg font-bold leading-none" style={{ color: "var(--primary)" }}>
                    {n}
                  </span>
                  <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em]" style={{ color: "var(--muted)" }}>
                    {label}
                  </span>
                </span>
              ))}
            </div>
          </Reveal>

          <Reveal delay={140}>
            <LiveTerminal themeId={themeId} />
          </Reveal>
        </section>

        {/* 01 · biblioteca */}
        <section id="biblioteca" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="01"
              kicker="biblioteca de temas"
              title="Escolha o tema — a página obedece"
              desc="O mesmo grid de seleção do profile.css: miniatura, nome e descrição. Aqui o clique aplica o tema instantaneamente e salva sua preferência, como a rota /profile/theme faria no Flask."
            />
          </Reveal>
          <Reveal delay={100}>
            <ThemeGrid active={themeId} onSelect={setThemeId} />
          </Reveal>
        </section>

        {/* 02 · prévia */}
        <section id="previa" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="02"
              kicker="prévia ao vivo"
              title="A carteira vestindo o tema"
              desc="Uma maquete funcional da tela principal do ControleRendaVariável: resumo do patrimônio, evolução da carteira e posições em ações, ETFs e opções. Tudo pintado pelos tokens do tema ativo."
              side={
                <span
                  className="hidden rounded-full border px-3.5 py-1.5 font-mono text-[0.66rem] uppercase tracking-[0.16em] lg:inline-block"
                  style={{ borderColor: "var(--border)", color: "var(--muted)", background: "var(--card)" }}
                >
                  tema ativo: {theme.label}
                </span>
              }
            />
          </Reveal>
          <Reveal delay={100}>
            <DashboardPreview />
          </Reveal>
        </section>

        {/* 03 · tokens */}
        <section id="tokens" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="03"
              kicker="inspetor de tokens"
              title="O que o tema injeta agora"
              desc="Cada tema é um bloco html[data-theme=...] com estes pares de variáveis. Inspecione os valores do tema ativo e copie qualquer um com um clique."
            />
          </Reveal>
          <Reveal delay={100}>
            <TokenInspector theme={theme} />
          </Reveal>
        </section>

        {/* 04 · patch pronto */}
        <section id="patch" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="04"
              kicker="patch pronto · lido do github"
              title="Os 4 passos, ajustados ao Projeto 4 real"
              desc="Li o app/static/app.css e o app/models.py direto do repositório. O patch fala as variáveis que o app já usa (--bg, --surface, --accent…) e traz o tema atual como o 13º — Original RV. No Windows, baixe o ZIP pelo botão flutuante: o aplicar_patch.bat instala direto em …\\VSCodeProjects\\ControleRendaVariavel com dois cliques."
            />
          </Reveal>
          <PatchSection />
        </section>

        {/* 05 · integração */}
        <section id="integracao" className="scroll-mt-24 py-10 sm:py-14">
          <Reveal>
            <SectionHead
              num="05"
              kicker="integração no flask · como funciona"
              title="O mecanismo, em quatro passos"
              desc="O roteiro que saiu da conversa para levar o sistema ao ControleRendaVariável: banco, rota, template base e CSS. A seção anterior tem os arquivos completos; aqui, a lógica de cada passo."
            />
          </Reveal>
          <IntegrationGuide />
        </section>
      </main>

      {/* rodapé */}
      <footer className="mt-8 border-t" style={{ borderColor: "var(--border)", background: "var(--bg-header)" }}>
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div className="flex items-center gap-2.5">
            <span
              className="flex h-7 w-7 items-center justify-center rounded-md text-[1rem]"
              style={{ background: "var(--primary-light)", color: "var(--primary)" }}
            >
              <CandlesIcon />
            </span>
            <p className="font-mono text-[0.68rem]" style={{ color: "var(--muted)" }}>
              Central de Temas · demo interativa do ControleRendaVariável
            </p>
          </div>
          <p className="max-w-md text-[0.72rem] leading-relaxed" style={{ color: "var(--muted)" }}>
            Conteúdo reproduzido da conversa no Qwen Chat sobre os projetos de{" "}
            <a
              href="https://github.com/mspa-coder"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 font-semibold transition-opacity hover:opacity-70"
              style={{ color: "var(--primary)" }}
            >
              github.com/mspa-coder <ArrowUpRightIcon className="text-[0.7rem]" />
            </a>{" "}
            — Flask, SQLAlchemy, Alembic e HTMX. Dados de mercado simulados.
          </p>
        </div>
      </footer>

      {/* flash de tema — espelha o flash() do Flask */}
      {toast && (
        <div
          key={toast.key}
          className="toast-in fixed bottom-5 right-5 z-50 flex items-center gap-3 rounded-[var(--radius)] border px-4 py-3 shadow-[0_18px_44px_-16px_rgba(0,0,0,0.5)]"
          role="status"
          style={{ background: "var(--card)", borderColor: "var(--border)", borderLeft: "3px solid var(--success)" }}
        >
          <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[0.8rem]"
            style={{ background: "var(--success)", color: theme.dark ? "#10131a" : "#ffffff" }}
          >
            <CheckIcon />
          </span>
          <span>
            <span className="block text-[0.82rem] font-semibold" style={{ color: "var(--text)" }}>
              {toast.msg}
            </span>
            <span className="block font-mono text-[0.64rem]" style={{ color: "var(--muted)" }}>
              preferência salva · localStorage["{STORAGE_KEY}"]
            </span>
          </span>
        </div>
      )}

      {/* assistente de download */}
      <DownloadAssist payload={gate} onClose={() => setGate(null)} />
    </div>
  );
}
