import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { currentRole, setSession } from "./api";
import Login from "./pages/Login";
import Queue from "./pages/Queue";
import ClaimReview from "./pages/ClaimReview";
import Credits from "./pages/Credits";
import Obligations from "./pages/Obligations";
import "./styles.css";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

function Shell({ children }: { children: React.ReactNode }) {
  const nav = useNavigate();
  return (
    <div className="shell">
      <header>
        <strong>POPs Review</strong>
        <nav>
          <NavLink to="/queue">Queue</NavLink>
          <NavLink to="/credits">Credits</NavLink>
          <NavLink to="/obligations">Obligations</NavLink>
        </nav>
        <span className="muted">{currentRole()}</span>
        <button
          className="ghost"
          onClick={() => {
            setSession(null);
            nav("/login");
          }}
        >
          Sign out
        </button>
      </header>
      <main>{children}</main>
    </div>
  );
}

function Authed({ children }: { children: React.ReactNode }) {
  return currentRole() ? <Shell>{children}</Shell> : <Navigate to="/login" replace />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/queue" element={<Authed><Queue /></Authed>} />
          <Route path="/claims/:id" element={<Authed><ClaimReview /></Authed>} />
          <Route path="/credits" element={<Authed><Credits /></Authed>} />
          <Route path="/obligations" element={<Authed><Obligations /></Authed>} />
          <Route path="*" element={<Navigate to="/queue" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
