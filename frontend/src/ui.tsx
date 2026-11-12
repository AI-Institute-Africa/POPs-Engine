// Small shared building blocks, so every page speaks the same visual language.
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { hasRole, switchPersona } from "./api";
import { personaForRole } from "./personas";
import { reasonText, stateInfo, type Tone } from "./labels";

export function Chip({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`chip2 tone-${tone}`} title={title}>
      {children}
    </span>
  );
}

// One status pill for claims and identity checks alike.
export function StateChip({ state }: { state: string }) {
  const s = stateInfo(state);
  return (
    <Chip tone={s.tone} title={s.hint}>
      <span className="chip-dot" aria-hidden />
      {s.label}
    </Chip>
  );
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="muted page-sub">{sub}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function EmptyState({ icon = "✓", title, children, action }: { icon?: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card empty-state">
      <div className="empty-glyph" aria-hidden>
        {icon}
      </div>
      <h2>{title}</h2>
      {children && <p className="muted">{children}</p>}
      {action}
    </div>
  );
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton-card" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton-line" style={{ width: `${90 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function Reasons({ codes, empty }: { codes: string[]; empty?: string }) {
  if (!codes.length) return empty ? <p className="muted small">{empty}</p> : null;
  return (
    <ul className="reasons-list">
      {codes.map((c) => (
        <li key={c}>
          <code className="reason-code">{c}</code>
          <span>{reasonText(c)}</span>
        </li>
      ))}
    </ul>
  );
}

// A link into the reviewer console. If the current persona cannot see the page, it offers the
// switch instead of a permission error.
export function InspectLink({ to, need = "reviewer", children }: { to: string; need?: string; children: ReactNode }) {
  if (hasRole(need)) {
    return (
      <Link className="btn secondary" to={to}>
        {children}
      </Link>
    );
  }
  const p = personaForRole(need);
  return (
    <button type="button" className="secondary" onClick={() => switchPersona(p.key, to)}>
      {children} <span className="muted small">(as {p.label})</span>
    </button>
  );
}

export function Avatar({ initials, color, size = 30 }: { initials: string; color: string; size?: number }) {
  return (
    <span className="avatar" style={{ background: color, width: size, height: size, fontSize: size * 0.4 }} aria-hidden>
      {initials}
    </span>
  );
}
