import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { currentRole, ensureSession } from "./api";
import { I18nProvider, LANG_LABELS, useI18n, type Lang } from "./i18n";
import { ToastProvider } from "./toast";
import { useTheme } from "./theme";
import Scan from "./pages/Scan";
import Guardrails from "./pages/Guardrails";
import "./styles.css";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function LangSwitch() {
  const { lang, setLang } = useI18n();
  return (
    <select
      className="lang-switch"
      value={lang}
      onChange={(e) => setLang(e.target.value as Lang)}
      aria-label="Language"
    >
      {(Object.keys(LANG_LABELS) as Lang[]).map((l) => (
        <option key={l} value={l}>
          {LANG_LABELS[l]}
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

function Shell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="shell">
      <header>
        <NavLink to="/" className="brand">
          <span className="logo" aria-hidden>V</span>
          <span className="brand-name">
            Validate<span className="dot">.ai</span>
          </span>
          <span className="pill">Receipt verification</span>
        </NavLink>
        <nav>
          <NavLink to="/" end>
            {t("nav.scan")}
          </NavLink>
          <NavLink to="/guardrails">{t("nav.guardrails")}</NavLink>
        </nav>
        <LangSwitch />
        <ThemeButton />
        <span className="muted small">{currentRole() ?? "local"}</span>
      </header>
      <main>{children}</main>
      <footer>
        <strong>Validate</strong> — {t("footer.text").replace("Validate — ", "")}
      </footer>
    </div>
  );
}

// No login screen: quietly obtain a dev session, then render.
void ensureSession().finally(() =>
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <QueryClientProvider client={qc}>
        <I18nProvider>
          <ToastProvider>
            <BrowserRouter>
              <Shell>
                <Routes>
                  <Route path="/" element={<Scan />} />
                  <Route path="/guardrails" element={<Guardrails />} />
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
