import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, del, hasRole, post } from "../api";

type SystemRule = { id: string; name: string; description: string };
type Rule = {
  rule_id: string;
  name: string;
  kind: string;
  action: string;
  config: Record<string, unknown>;
  description: string;
  enabled: boolean;
  created_by: string;
  created_at: string;
  removed_by: string | null;
  removed_at: string | null;
};
type Listing = {
  system: SystemRule[];
  rules: Rule[];
  reader: { model: string; external: boolean; ai_enabled: boolean; disabled_by: string[]; hints: string[] };
};
type Template = { name: string; kind: string; action: string; description: string; config: Record<string, unknown> };
type PolicyVersion = { policy_id: string; version: string; active: boolean; created_by: string; created_at: string; config: Record<string, unknown> };

// Each kind with its allowed action and a starter config, mirroring the server's validation.
const KINDS: Record<string, { action: string; help: string; config: Record<string, unknown> }> = {
  blocked_text: { action: "review", help: "Send to review when any field text matches a pattern.", config: { patterns: ["ignore (all|previous) instructions"] } },
  field_pattern: { action: "review", help: "Distrust a field that does not fully match a pattern (optionally for one provider).", config: { field: "transaction_id", provider: "ECOCASH", pattern: "(?:PP|MP|CO)\\d{6}\\.\\d{4}\\.[A-Z0-9]{6}" } },
  amount_range: { action: "review", help: "Review amounts outside a range (minor units, e.g. 100 = 1.00).", config: { currency: "USD", min_minor: 100, max_minor: 500000 } },
  require_field: { action: "needs_info", help: "Ask for a clearer copy, or review, when a field is missing.", config: { field: "transaction_id", must_agree: false } },
  min_confidence: { action: "review", help: "Distrust a field read below a confidence.", config: { field: "amount", min: 0.9 } },
  prompt_instruction: { action: "prompt", help: "Reading hint appended to the model prompt. Cannot change output or rules.", config: { text: "Dates are day-first (DD/MM/YYYY)." } },
  model_allowlist: { action: "disable_ai", help: "Switch the AI reader off unless the configured model is listed.", config: { models: ["qwen3-vl:235b"] } },
  external_processing_block: { action: "disable_ai", help: "Never send receipts to an external AI service.", config: {} },
  ai_kill_switch: { action: "disable_ai", help: "Turn the AI reader off for this organisation.", config: {} },
};

const ACTION_LABEL: Record<string, string> = {
  review: "send to review",
  needs_info: "ask payer for info",
  disable_ai: "switch AI off",
  prompt: "reading hint",
};

function RuleForm({ onDone }: { onDone: () => void }) {
  const [kind, setKind] = useState("blocked_text");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [action, setAction] = useState(KINDS.blocked_text.action);
  const [config, setConfig] = useState(JSON.stringify(KINDS.blocked_text.config, null, 2));
  const add = useMutation({
    mutationFn: () => post("/admin/guardrails", { name, kind, action, description, config: JSON.parse(config) }),
    onSuccess: () => {
      setName("");
      setDescription("");
      onDone();
    },
  });
  const pick = (k: string) => {
    setKind(k);
    setAction(KINDS[k].action);
    setConfig(JSON.stringify(KINDS[k].config, null, 2));
  };
  let parseError: string | null = null;
  try {
    JSON.parse(config);
  } catch (e) {
    parseError = (e as Error).message;
  }
  return (
    <form className="rule-form" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
      <div className="inline">
        <select value={kind} onChange={(e) => pick(e.target.value)} aria-label="Rule kind">
          {Object.keys(KINDS).map((k) => <option key={k} value={k}>{k.replace(/_/g, " ")}</option>)}
        </select>
        {(kind === "field_pattern" || kind === "blocked_text" || kind === "amount_range" || kind === "require_field" || kind === "min_confidence") && (
          <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action">
            <option value="review">send to review</option>
            <option value="needs_info">ask payer for info</option>
          </select>
        )}
        <input placeholder="Rule name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <p className="muted small">{KINDS[kind].help}</p>
      <input placeholder="Why this rule exists (shown to reviewers)" value={description} onChange={(e) => setDescription(e.target.value)} />
      <textarea rows={5} value={config} onChange={(e) => setConfig(e.target.value)} aria-label="Rule configuration (JSON)" spellCheck={false} />
      {parseError && <p className="error small">Config is not valid JSON: {parseError}</p>}
      {add.error && <p className="error">{(add.error as Error).message}</p>}
      <button disabled={name.length < 3 || !!parseError || add.isPending}>Add guardrail</button>
    </form>
  );
}

function PolicyPanel({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const current = useQuery({ queryKey: ["policy"], queryFn: () => api<{ version: string; config: Record<string, unknown> }>("/admin/policy") });
  const history = useQuery({ queryKey: ["policies"], queryFn: () => api<PolicyVersion[]>("/admin/policies") });
  const [draft, setDraft] = useState<string | null>(null);
  const [version, setVersion] = useState("");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["policy"] });
    qc.invalidateQueries({ queryKey: ["policies"] });
  };
  const publish = useMutation({
    mutationFn: () => post("/admin/policy", { version, config: JSON.parse(draft ?? "{}") }),
    onSuccess: () => { setDraft(null); setVersion(""); refresh(); },
  });
  const retire = useMutation({
    mutationFn: (id: string) => post(`/admin/policies/${id}/deactivate`, { reason: "retired from console" }),
    onSuccess: refresh,
  });
  const text = draft ?? JSON.stringify(current.data?.config ?? {}, null, 2);
  return (
    <div className="card">
      <h2>Payment policy</h2>
      <p className="muted small">
        Thresholds the rules and reviewers work to: date window, receiving accounts, per-currency second-approver limits, manual-credit role,
        pilot channels. Auto-approval cannot be enabled in this release.
      </p>
      <p>Active version <code>{current.data?.version}</code></p>
      <textarea rows={14} value={text} readOnly={!admin} onChange={(e) => setDraft(e.target.value)} spellCheck={false} aria-label="Policy configuration (JSON)" />
      {admin && (
        <form className="inline" onSubmit={(e) => { e.preventDefault(); publish.mutate(); }}>
          <input placeholder="New version name, e.g. 2026.11-pilot" value={version} onChange={(e) => setVersion(e.target.value)} />
          <button disabled={!version || draft === null || publish.isPending}>Publish new version</button>
          {draft !== null && <button type="button" className="ghost" onClick={() => setDraft(null)}>Discard edits</button>}
        </form>
      )}
      {(publish.error || retire.error) && <p className="error">{((publish.error ?? retire.error) as Error).message}</p>}
      <h3>History</h3>
      <table>
        <thead><tr><th>Version</th><th>Status</th><th>Published by</th><th>When</th><th></th></tr></thead>
        <tbody>
          {history.data?.map((v) => (
            <tr key={v.policy_id}>
              <td><code>{v.version}</code></td>
              <td>{v.active ? "active" : "retired"}</td>
              <td>{v.created_by}</td>
              <td>{new Date(v.created_at).toLocaleString()}</td>
              <td>{admin && v.active && <button className="ghost small" onClick={() => retire.mutate(v.policy_id)}>Retire</button>}</td>
            </tr>
          ))}
          {history.data?.length === 0 && <tr><td colSpan={5} className="muted">Using the built-in default policy.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

export default function Guardrails() {
  const qc = useQueryClient();
  const admin = hasRole("admin");
  const [showRemoved, setShowRemoved] = useState(false);
  const list = useQuery({ queryKey: ["guardrails", showRemoved], queryFn: () => api<Listing>(`/admin/guardrails?include_removed=${showRemoved}`) });
  const templates = useQuery({ queryKey: ["guardrail-templates"], queryFn: () => api<Template[]>("/admin/guardrails/templates") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["guardrails"] });
  const addTemplate = useMutation({ mutationFn: (t: Template) => post("/admin/guardrails", t), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: (r: Rule) => post(`/admin/guardrails/${r.rule_id}/enabled`, { enabled: !r.enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => del(`/admin/guardrails/${id}`, { reason }),
    onSuccess: refresh,
  });
  const err = (addTemplate.error ?? toggle.error ?? remove.error) as Error | null;
  const active = new Set(list.data?.rules.filter((r) => !r.removed_at).map((r) => r.name));
  const reader = list.data?.reader;

  return (
    <section>
      <div className="toolbar">
        <h2>Guardrails &amp; policies for the AI</h2>
        {!admin && <span className="muted small">Read-only: only admins can add or remove guardrails.</span>}
      </div>
      {err && <p className="error">{err.message}</p>}

      {reader && (
        <div className={`card reader ${reader.ai_enabled ? "" : "off"}`}>
          <strong>AI reader: {reader.ai_enabled ? "on" : "off"}</strong> · model <code>{reader.model}</code>
          {reader.external && <span className="badge tier-amber">external service</span>}
          {!reader.ai_enabled && <span className="muted"> · switched off by {reader.disabled_by.join(", ")} — claims are read by OCR only and go to review</span>}
          {reader.hints.length > 0 && <div className="muted small">Reading hints: {reader.hints.join(" · ")}</div>}
        </div>
      )}

      <div className="split even">
        <div>
          <div className="card">
            <h2>Locked guardrails</h2>
            <p className="muted small">Enforced in code and the database. They cannot be removed.</p>
            <ul className="locked">
              {list.data?.system.map((g) => (
                <li key={g.id}>
                  <span aria-hidden>🔒</span> <strong>{g.name}</strong>
                  <div className="muted small">{g.description}</div>
                </li>
              ))}
            </ul>
          </div>

          <div className="card">
            <div className="toolbar">
              <h2>Your guardrails</h2>
              <label className="small"><input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} /> show removed</label>
            </div>
            <p className="muted small">Guardrails only make the pipeline stricter: they can send claims to review, ask for information, distrust a reading, add reading hints or switch the AI off. None can approve.</p>
            {list.data?.rules.length === 0 && <p className="muted">No custom guardrails yet. Start from a recommended one below.</p>}
            {list.data?.rules.map((r) => (
              <div key={r.rule_id} className={`rule ${r.enabled ? "" : "disabled"} ${r.removed_at ? "removed" : ""}`}>
                <div className="row">
                  <strong>{r.name}</strong>
                  <span className="badge">{r.kind.replace(/_/g, " ")}</span>
                  <span className="badge">{ACTION_LABEL[r.action] ?? r.action}</span>
                  {r.removed_at ? (
                    <span className="muted small">removed by {r.removed_by} {new Date(r.removed_at).toLocaleDateString()}</span>
                  ) : (
                    !r.enabled && <span className="badge tier-amber">disabled</span>
                  )}
                  {admin && !r.removed_at && (
                    <span className="actions-right">
                      <button className="ghost small" onClick={() => toggle.mutate(r)}>{r.enabled ? "Disable" : "Enable"}</button>
                      <button
                        className="danger small"
                        onClick={() => {
                          const reason = window.prompt(`Why remove "${r.name}"? (kept in the audit log)`);
                          if (reason && reason.length >= 3) remove.mutate({ id: r.rule_id, reason });
                        }}
                      >
                        Remove
                      </button>
                    </span>
                  )}
                </div>
                {r.description && <div className="muted small">{r.description}</div>}
                <code className="config">{JSON.stringify(r.config)}</code>
              </div>
            ))}
          </div>

          {admin && (
            <div className="card">
              <h2>Add a guardrail</h2>
              <RuleForm onDone={refresh} />
            </div>
          )}
        </div>

        <div>
          <div className="card">
            <h2>Recommended</h2>
            {templates.data?.map((t) => (
              <div key={t.name} className="rule">
                <div className="row">
                  <strong>{t.name}</strong>
                  <span className="badge">{ACTION_LABEL[t.action]}</span>
                  {admin && (
                    <span className="actions-right">
                      <button className="small" disabled={active.has(t.name) || addTemplate.isPending} onClick={() => addTemplate.mutate(t)}>
                        {active.has(t.name) ? "Added" : "Add"}
                      </button>
                    </span>
                  )}
                </div>
                <div className="muted small">{t.description}</div>
              </div>
            ))}
          </div>
          <PolicyPanel admin={admin} />
        </div>
      </div>
    </section>
  );
}
