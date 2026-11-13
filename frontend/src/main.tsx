import React, { useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider, useMutation, useQuery } from "@tanstack/react-query";
import { BrowserRouter, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, currentPersona, hasRole, switchPersona, ensureSession } from "./api";
import { demoInfo, loadDemo, resetDemo } from "./demo";
import { I18nProvider, LANG_LABELS, langLabel, useI18n, type Lang } from "./i18n";
import { PERSONA_LIST } from "./personas";
import { ToastProvider, useToast } from "./toast";
import { useTheme } from "./theme";
import { Avatar } from "./ui";
import Home from "./pages/Home";
import Scan from "./pages/Scan";
import Track from "./pages/Track";
import ImageToText from "./pages/ImageToText";
import Guardrails from "./pages/Guardrails";
import Fraud from "./pages/Fraud";
import Alerts from "./pages/Alerts";
import Replay from "./pages/Replay";
import Queue from "./pages/Queue";
import ClaimReview from "./pages/ClaimReview";
import Credits from "./pages/Credits";
import Obligations from "./pages/Obligations";
import Exposure from "./pages/Exposure";
import AutoApproved from "./pages/AutoApproved";
import Kyc from "./pages/Kyc";
import KycReview from "./pages/KycReview";
import "./styles.css";
import "./ui.css";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function LangSwitch() {
  const { lang, setLang } = useI18n();
  return (
    <select className="lang-switch" value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label="Language">
      {(Object.keys(LANG_LABELS) as Lang[]).map((l) => (
        <option key={l} value={l}>
          {langLabel(l)}
        </option>
      ))}
    </select>
  );
}

function ThemeButton() {
  const { cycle, icon, label } = useTheme();
  return (
    <button type="button" className="ghost icon-btn" onClick={cycle} title={label} aria-label={label}>
      <span aria-hidden>{icon}</span>
    </button>
  );
}

function Count({ queryKey, path, pick }: { queryKey: string; path: string; pick?: (d: any) => number }) {
  const q = useQuery({ queryKey: [queryKey], queryFn: () => api<any>(path), refetchInterval: 20000, enabled: hasRole("reviewer") });
  const n = q.data ? (pick ? pick(q.data) : q.data.length) : 0;
  return n > 0 ? <span className="count">{n}</span> : null;
}

type Item = { to: string; icon: string; label: string; min?: string; end?: boolean; badge?: React.ReactNode };

function groups(t: (k: string) => string): { title: string; items: Item[] }[] {
  return [
    {
      title: "Start",
      items: [{ to: "/", icon: "🏠", label: "Home & tour", end: true }],
    },
    {
      title: "Payments",
      items: [
        { to: "/verify", icon: "🧾", label: t("nav.scan") === "nav.scan" ? "Verify a payment" : "Verify a payment" },
        { to: "/image-to-text", icon: "🔤", label: t("nav.imageToText") === "nav.imageToText" ? "Image to text" : t("nav.imageToText") },
        { to: "/queue", icon: "📥", label: "Review queue", min: "reviewer", badge: <Count queryKey="queue-count" path="/review/queue" /> },
        { to: "/fraud", icon: "🛡️", label: "Suspected fakes", min: "reviewer", badge: <Count queryKey="fraud-summary" path="/fraud/alerts/summary" pick={(d) => d.open_high ?? 0} /> },
        { to: "/credits", icon: "🏦", label: "Bank credits", min: "reviewer" },
        { to: "/obligations", icon: "📄", label: "Invoices", min: "reviewer" },
        { to: "/auto", icon: "⚡", label: "Auto-approved", min: "reviewer" },
      ],
    },
    {
      title: "Identity",
      items: [
        { to: "/kyc", icon: "🪪", label: "Verify an ID" },
        { to: "/kyc/review", icon: "🔍", label: "ID checks", min: "reviewer", end: true, badge: <Count queryKey="kyc-count" path="/kyc/review/queue" /> },
      ],
    },
    {
      title: "Operations",
      items: [
        { to: "/alerts", icon: "🔔", label: "Payment alerts", min: "reviewer" },
        { to: "/exposure", icon: "📊", label: "Exposure", min: "admin" },
        { to: "/guardrails", icon: "🚧", label: t("nav.guardrails") === "nav.guardrails" ? "Guardrails" : t("nav.guardrails"), min: "admin" },
        { to: "/replay", icon: "🧪", label: "Replay & accuracy", min: "admin" },
      ],
    },
  ];
}

const TITLES: [string, string][] = [
  ["/kyc/review", "ID checks"],
  ["/kyc", "Verify an ID"],
  ["/verify", "Verify a payment"],
  ["/image-to-text", "Image to text"],
  ["/queue", "Review queue"],
  ["/claims", "Claim"],
  ["/fraud", "Suspected fakes"],
  ["/credits", "Bank credits"],
  ["/obligations", "Invoices"],
  ["/auto", "Auto-approved"],
  ["/alerts", "Payment alerts"],
  ["/exposure", "Exposure"],
  ["/guardrails", "Guardrails"],
  ["/replay", "Replay & accuracy"],
];

function useTitle(): string {
  const { pathname } = useLocation();
  if (pathname === "/") return "Home";
  return TITLES.find(([p]) => pathname === p || pathname.startsWith(p + "/"))?.[1] ?? "Validate";
}

function PersonaMenu({ onReset }: { onReset: () => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const me = currentPersona();
  useEffect(() => {
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, []);
  return (
    <div className="persona" ref={box}>
      <button type="button" className="persona-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <Avatar initials={me.initials} color={me.color} />
        <span className="lbl">
          {me.label} <span className="small">▾</span>
        </span>
      </button>
      {open && (
        <div className="persona-menu" role="menu">
          <div className="head">View the product as…</div>
          {PERSONA_LIST.map((p) => (
            <button key={p.key} type="button" role="menuitem" className={`persona-item${p.key === me.key ? " on" : ""}`} onClick={() => switchPersona(p.key, "/")}>
              <Avatar initials={p.initials} color={p.color} size={34} />
              <span>
                <b>
                  {p.label} <span className="muted small">· {p.who}</span>
                </b>
                <span className="d">{p.blurb}</span>
              </span>
            </button>
          ))}
          {demoInfo() && (
            <>
              <hr />
              <button type="button" role="menuitem" className="persona-item" onClick={() => { setOpen(false); onReset(); }}>
                <span className="avatar" style={{ background: "var(--muted)", width: 34, height: 34 }} aria-hidden>↺</span>
                <span>
                  <b>Reset demo data</b>
                  <span className="d">Wipe everything and start again</span>
                </span>
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const title = useTitle();
  const toast = useToast();
  const [menu, setMenu] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setMenu(false), [pathname]);
  const reset = useMutation({
    mutationFn: resetDemo,
    onSuccess: () => window.location.assign("/"),
    onError: (e) => toast("err", (e as Error).message),
  });
  const confirmReset = () => {
    if (window.confirm("Wipe all demo data and start again? This takes about 15 seconds.")) reset.mutate();
  };
  const demo = demoInfo();
  return (
    <div className="app">
      {demo && (
        <div className="demo-bar" role="note">
          <b>DEMO</b>
          <span>
            Everything here is synthetic. Documents are fictional and the readers and face matcher are simulated.
          </span>
        </div>
      )}
      <aside className={`sidebar${menu ? " open" : ""}`} aria-label="Main navigation">
        <NavLink to="/" className="brand">
          <span className="logo" aria-hidden>V</span>
          <span className="brand-name">
            Validate<span className="dot">.ai</span>
          </span>
        </NavLink>
        <nav>
          {groups(t).map((g) => {
            const items = g.items.filter((i) => !i.min || hasRole(i.min));
            if (!items.length) return null;
            return (
              <div className="nav-group" key={g.title}>
                <div className="nav-title">{g.title}</div>
                {items.map((i) => (
                  <NavLink key={i.to} to={i.to} end={i.end ?? i.to === "/"}>
                    <span className="ico" aria-hidden>{i.icon}</span>
                    {i.label}
                    {i.badge}
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <div className="side-foot">
          A receipt is a claim, not a confirmation.
          <br />
          The AI reads. Rules and people decide.
        </div>
      </aside>
      <div className={`scrim${menu ? " show" : ""}`} onClick={() => setMenu(false)} />
      <div className="content">
        <header className="topbar">
          <button type="button" className="menu-btn" onClick={() => setMenu(true)} aria-label="Open menu">☰</button>
          <span className="crumb">{title}</span>
          <span className="spacer" />
          <LangSwitch />
          <ThemeButton />
          <PersonaMenu onReset={confirmReset} />
        </header>
        <main id="main">{children}</main>
        <footer>
          <strong>Validate</strong> — know that a payment, or a person, is real. {demo ? "Demo build with synthetic data." : ""}
        </footer>
      </div>
      {reset.isPending && (
        <div className="overlay" role="alertdialog" aria-live="polite">
          <div className="modal">
            <div className="spinner" />
            <h2>Resetting the demo</h2>
            <p className="muted">Clearing everything and replaying the sample stories. About 15 seconds.</p>
          </div>
        </div>
      )}
    </div>
  );
}

// No login screen: quietly sign in as a demo persona, learn whether this is the demo, then render.
void ensureSession()
  .then(() => loadDemo())
  .finally(() =>
    ReactDOM.createRoot(document.getElementById("root")!).render(
      <React.StrictMode>
        <QueryClientProvider client={qc}>
          <I18nProvider>
            <ToastProvider>
              <BrowserRouter>
                <Shell>
                  <Routes>
                    <Route path="/" element={<Home />} />
                    <Route path="/verify" element={<Scan />} />
                    <Route path="/image-to-text" element={<ImageToText />} />
                    <Route path="/queue" element={<Queue />} />
                    <Route path="/claims/:id" element={<ClaimReview />} />
                    <Route path="/track/:id" element={<Track />} />
                    <Route path="/credits" element={<Credits />} />
                    <Route path="/obligations" element={<Obligations />} />
                    <Route path="/exposure" element={<Exposure />} />
                    <Route path="/auto" element={<AutoApproved />} />
                    <Route path="/fraud" element={<Fraud />} />
                    <Route path="/guardrails" element={<Guardrails />} />
                    <Route path="/alerts" element={<Alerts />} />
                    <Route path="/replay" element={<Replay />} />
                    <Route path="/kyc" element={<Kyc />} />
                    <Route path="/kyc/review" element={<KycReview />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </Shell>
              </BrowserRouter>
            </ToastProvider>
          </I18nProvider>
        </QueryClientProvider>
      </React.StrictMode>,
    ),
  );
