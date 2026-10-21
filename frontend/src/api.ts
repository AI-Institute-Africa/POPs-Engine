// Thin typed client for the POPs Engine API. The server is authoritative for every permission.

export const API = import.meta.env.VITE_API_BASE ?? "/api";

export type Gate = { name: string; passed: boolean; detail: string };

export type Claim = {
  claim_id: string;
  obligation_id: string;
  claimant_ref: string;
  channel: string;
  declared_amount_minor: number | null;
  declared_currency: string | null;
  declared_reference: string | null;
  state: string;
  outcome: string;
  evidence_tier: string;
  approval_tier: string | null;
  reason_codes: string[];
  messages: string[];
  candidate_credit_id: string | null;
  created_at: string;
  updated_at: string;
};

export type Obligation = {
  obligation_id: string;
  learner_ref: string;
  type: string;
  description: string;
  amount_minor: number;
  currency: string;
  payment_code: string;
  external_ref: string | null;
  created_at: string;
};

export type Credit = {
  credit_id: string;
  source: string;
  provider: string;
  provider_txn_id: string;
  amount_minor: number;
  currency: string;
  occurred_at: string;
  status: string;
  beneficiary: string;
  payer: string | null;
  narrative: string | null;
  created_by: string;
  available_minor: number | null;
};

export type Field = {
  field_id: string;
  name: string;
  raw_value: string | null;
  normalised_value: string | null;
  confidence: number;
  extractor: string;
  extractor_version: string;
  region: { x: number; y: number; w: number; h: number } | null;
  agreed: boolean;
  corrected_by: string | null;
  correction_reason: string | null;
};

export type Candidate = {
  credit: Credit;
  passed: boolean;
  score: number;
  gates: Gate[];
  components: Record<string, number>;
  reference_kind: string;
};

export type ReviewDetail = {
  claim: Claim;
  obligation: Obligation;
  balance: {
    amount_minor: number;
    paid_minor: number;
    outstanding_minor: number;
    currency: string;
    payment_status: string;
  };
  evidence: { evidence_id: string; preview_url: string; original_url: string; sha256: string; content_type: string; meta: Record<string, unknown> }[];
  fields: Field[];
  candidates: Candidate[];
  decisions: { decision_id: string; outcome: string; reason_codes: string[]; actor_type: string; actor_id: string; policy_version: string; note: string | null; created_at: string }[];
  allocations: { allocation_id: string; kind: string; amount_minor: number; currency: string; created_at: string; reverses: string | null }[];
  related_claims: string[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Tokens live in sessionStorage (cleared when the tab closes), never localStorage.
// With OIDC configured, oidc-client-ts manages the session and we read its access token.
function store(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

let oidcToken: string | null = null;
export function setOidcToken(t: string | null) {
  oidcToken = t;
}

function token(): string | null {
  return oidcToken ?? store()?.getItem("pops-token") ?? null;
}

export function setSession(tok: string | null, role?: string) {
  const s = store();
  if (!s) return;
  if (tok) {
    s.setItem("pops-token", tok);
    if (role) s.setItem("pops-role", role);
  } else {
    s.removeItem("pops-token");
    s.removeItem("pops-role");
    oidcToken = null;
  }
}

export function setRole(role: string) {
  store()?.setItem("pops-role", role);
}

export function currentRole(): string | null {
  return store()?.getItem("pops-role") ?? null;
}

const RANK: Record<string, number> = { submitter: 0, reviewer: 1, approver: 2, admin: 3 };
export function hasRole(min: string): boolean {
  return (RANK[currentRole() ?? ""] ?? -1) >= RANK[min];
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const t = token();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      const d = body.detail;
      msg = typeof d === "string" ? d : d?.message ?? JSON.stringify(d);
    } catch {
      /* non-JSON error */
    }
    if (res.status === 401) setSession(null);
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body) });

export const del = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "DELETE", body: JSON.stringify(body) });

export function money(minor: number | null | undefined, currency?: string | null): string {
  if (minor === null || minor === undefined) return "—";
  return `${currency ?? ""} ${(minor / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();
}

export function ago(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `${mins}m`;
  if (mins < 1440) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}
