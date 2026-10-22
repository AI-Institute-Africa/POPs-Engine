import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, del, hasRole, post, type ShadowScorecard } from "../api";
import { useToast } from "../toast";

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
type AuditEvent = { actor: string; action: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null; created_at: string };

const FIELDS = ["provider", "document_type", "transaction_id", "amount", "currency", "date", "sender", "beneficiary", "status", "narrative"];

const ACTION_LABEL: Record<string, string> = {
  review: "send to review",
  needs_info: "ask payer for info",
  disable_ai: "switch AI off",
  prompt: "reading hint",
};

// Per-kind help text for the structured editor.
const KIND_HELP: Record<string, string> = {
  blocked_text: "Send to review when any field text matches one of these patterns.",
  field_pattern: "Distrust a field that does not fully match the pattern.",
  amount_range: "Review amounts outside this range (whole currency units).",
  require_field: "Ask for a clearer copy, or review, when a field is missing.",
  min_confidence: "Distrust a field read below this confidence.",
  prompt_instruction: "Reading hint appended to the model prompt. Cannot change output or rules.",
  model_allowlist: "Switch the AI off unless the listed model is in use.",
  external_processing_block: "Never send receipts to an external AI service.",
  ai_kill_switch: "Turn the AI reader off for this organisation.",
};

// ── structured per-kind config editor ────────────────────────────────────────────────
function ConfigEditor({ kind, config, onChange }: { kind: string; config: Record<string, unknown>; onChange: (c: Record<string, unknown>) => void }) {
  const set = (k: string, v: unknown) => onChange({ ...config, [k]: v });
  const str = (k: string) => (typeof config[k] === "string" ? (config[k] as string) : "");

  if (kind === "blocked_text") {
    const patterns = Array.isArray(config.patterns) ? (config.patterns as string[]) : [];
    return (
      <>
        <label>
          Patterns <span className="muted">(one regex per line)</span>
          <textarea
            rows={3}
            value={patterns.join("\n")}
            onChange={(e) => set("patterns", e.target.value.split("\n").filter((l) => l.trim()))}
            spellCheck={false}
          />
        </label>
        <label>
          Only these fields <span className="muted">(optional, comma-separated)</span>
          <input
            placeholder="transaction_id, amount"
            onChange={(e) => {
              const list = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
              set("fields", list.length ? list : undefined);
            }}
          />
        </label>
      </>
    );
  }
  if (kind === "field_pattern") {
    return (
      <>
        <label>
          Field
          <select value={str("field") || "transaction_id"} onChange={(e) => set("field", e.target.value)}>
            {FIELDS.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label>
          Pattern <span className="muted">(regex)</span>
          <input value={str("pattern")} onChange={(e) => set("pattern", e.target.value)} spellCheck={false} />
        </label>
        <label>
          Only for provider <span className="muted">(optional, e.g. ECOCASH)</span>
          <input value={str("provider")} onChange={(e) => set("provider", e.target.value || undefined)} />
        </label>
      </>
    );
  }
  if (kind === "amount_range") {
    const toMinor = (v: string) => (v.trim() ? Math.round(parseFloat(v) * 100) : undefined);
    const toMajor = (v: unknown) => (typeof v === "number" ? String(v / 100) : "");
    return (
      <>
        <label>
          Currency
          <input value={str("currency") || "USD"} maxLength={3} onChange={(e) => set("currency", e.target.value.toUpperCase())} />
        </label>
        <div className="inline-pair">
          <label>
            Minimum
            <input inputMode="decimal" placeholder="1.00" defaultValue={toMajor(config.min_minor)} onBlur={(e) => set("min_minor", toMinor(e.target.value))} />
          </label>
          <label>
            Maximum
            <input inputMode="decimal" placeholder="5000.00" defaultValue={toMajor(config.max_minor)} onBlur={(e) => set("max_minor", toMinor(e.target.value))} />
          </label>
        </div>
      </>
    );
  }
  if (kind === "require_field") {
    return (
      <>
        <label>
          Field
          <select value={str("field") || "transaction_id"} onChange={(e) => set("field", e.target.value)}>
            {FIELDS.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={config.must_agree !== false} onChange={(e) => set("must_agree", e.target.checked)} />
          Both readers must agree on it
        </label>
      </>
    );
  }
  if (kind === "min_confidence") {
    const v = typeof config.min === "number" ? config.min : 0.9;
    return (
      <label>
        Confidence floor: <strong>{v.toFixed(2)}</strong>
        <input type="range" min={0.5} max={1} step={0.01} value={v} onChange={(e) => set("min", parseFloat(e.target.value))} />
      </label>
    );
  }
  if (kind === "prompt_instruction") {
    return (
      <label>
        Reading hint
        <textarea rows={2} value={str("text")} onChange={(e) => set("text", e.target.value)} placeholder="Dates are day-first (DD/MM/YYYY)." />
      </label>
    );
  }
  if (kind === "model_allowlist") {
    const models = Array.isArray(config.models) ? (config.models as string[]) : [];
    return (
      <label>
        Allowed models <span className="muted">(one per line)</span>
        <textarea rows={2} value={models.join("\n")} onChange={(e) => set("models", e.target.value.split("\n").filter((l) => l.trim()))} spellCheck={false} />
      </label>
    );
  }
  return <p className="muted small">This guardrail needs no configuration.</p>;
}

function RuleForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [kind, setKind] = useState("blocked_text");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [action, setAction] = useState("review");
  const [advanced, setAdvanced] = useState(false);
  const [config, setConfig] = useState<Record<string, unknown>>({ patterns: ["ignore (all|previous) instructions"] });
  const [configJson, setConfigJson] = useState<string | null>(null);
  const add = useMutation({
    mutationFn: () => post("/admin/guardrails", { name, kind, action, description, config: configJson ? JSON.parse(configJson) : config }),
    onSuccess: () => {
      toast("ok", `Guardrail "${name}" added.`);
      setName("");
      setDescription("");
      onDone();
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  const DEFAULT_CONFIG: Record<string, Record<string, unknown>> = {
    blocked_text: { patterns: ["ignore (all|previous) instructions"] },
    field_pattern: { field: "transaction_id", pattern: "(?:PP|MP|CO)\\d{6}\\.\\d{4}\\.[A-Z0-9]{6}" },
    amount_range: { currency: "USD", max_minor: 500000 },
    require_field: { field: "transaction_id", must_agree: false },
    min_confidence: { field: "amount", min: 0.9 },
    prompt_instruction: { text: "Dates are day-first (DD/MM/YYYY)." },
    model_allowlist: { models: ["qwen3-vl:235b"] },
    external_processing_block: {},
    ai_kill_switch: {},
  };
  const ACTION_FOR: Record<string, string> = {
    prompt_instruction: "prompt",
    model_allowlist: "disable_ai",
    external_processing_block: "disable_ai",
    ai_kill_switch: "disable_ai",
  };

  const pick = (k: string) => {
    setKind(k);
    setAction(ACTION_FOR[k] ?? "review");
    setConfig(DEFAULT_CONFIG[k] ?? {});
    setConfigJson(null);
  };

  const jsonText = configJson ?? JSON.stringify(config, null, 2);
  let parseError: string | null = null;
  try {
    JSON.parse(jsonText);
  } catch (e) {
    parseError = (e as Error).message;
  }

  return (
    <form
      className="rule-form"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <div className="inline">
        <select value={kind} onChange={(e) => pick(e.target.value)} aria-label="Rule kind">
          {Object.keys(KIND_HELP).map((k) => (
            <option key={k} value={k}>
              {k.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        {kind !== "prompt_instruction" && !ACTION_FOR[kind] && (
          <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action">
            <option value="review">send to review</option>
            <option value="needs_info">ask payer for info</option>
          </select>
        )}
        <input placeholder="Rule name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <p className="muted small">{KIND_HELP[kind]}</p>
      <input placeholder="Why this rule exists (shown to reviewers)" value={description} onChange={(e) => setDescription(e.target.value)} />

      {advanced ? (
        <>
          <textarea rows={5} value={jsonText} onChange={(e) => setConfigJson(e.target.value)} aria-label="Rule configuration (JSON)" spellCheck={false} />
          {parseError && <p className="error small">Config is not valid JSON: {parseError}</p>}
        </>
      ) : (
        <ConfigEditor kind={kind} config={config} onChange={setConfig} />
      )}

      <label className="check small">
        <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} />
        Advanced: edit raw JSON
      </label>
      {add.error && <p className="error">{(add.error as Error).message}</p>}
      <button disabled={name.length < 3 || (advanced && !!parseError) || add.isPending}>Add guardrail</button>
    </form>
  );
}

// ── audit timeline for one rule ──────────────────────────────────────────────────────
function AuditTimeline({ ruleId }: { ruleId: string }) {
  const events = useQuery({
    queryKey: ["audit", ruleId],
    queryFn: () => api<AuditEvent[]>(`/audit?entity_id=${ruleId}&limit=50`),
  });
  if (events.isFetching) return <p className="muted small">Loading history…</p>;
  if (events.isError) return <p className="error small">{(events.error as Error).message}</p>;
  if (!events.data?.length) return <p className="muted small">No recorded changes.</p>;
  return (
    <ol className="timeline">
      {events.data.map((e, i) => (
        <li key={i}>
          <span className="dot" aria-hidden />
          <strong>{e.action.replace(/[._]/g, " ")}</strong> by {e.actor}
          <span className="muted small"> · {new Date(e.created_at).toLocaleString()}</span>
          {(e.before || e.after) && (
            <div className="muted small mono">
              {e.before ? `before: ${JSON.stringify(e.before)} · ` : ""}
              {e.after ? `after: ${JSON.stringify(e.after)}` : ""}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

// ── policy diff: draft vs active ─────────────────────────────────────────────────────
function policyDiff(active: Record<string, unknown>, draft: Record<string, unknown>) {
  const keys = [...new Set([...Object.keys(active), ...Object.keys(draft)])];
  return keys
    .filter((k) => JSON.stringify(active[k]) !== JSON.stringify(draft[k]))
    .map((k) => ({
      key: k,
      kind: !(k in active) ? "added" : !(k in draft) ? "removed" : "changed",
      before: active[k],
      after: draft[k],
    }));
}

function PolicyPanel() {
  const qc = useQueryClient();
  const toast = useToast();
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
    onSuccess: () => {
      toast("ok", `Policy version "${version}" published.`);
      setDraft(null);
      setVersion("");
      refresh();
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const retire = useMutation({
    mutationFn: (id: string) => post(`/admin/policies/${id}/deactivate`, { reason: "retired from console" }),
    onSuccess: () => {
      toast("info", "Policy version retired.");
      refresh();
    },
  });
  const text = draft ?? JSON.stringify(current.data?.config ?? {}, null, 2);
  let diff: ReturnType<typeof policyDiff> = [];
  let diffError: string | null = null;
  if (draft !== null) {
    try {
      diff = policyDiff(current.data?.config ?? {}, JSON.parse(draft));
    } catch (e) {
      diffError = (e as Error).message;
    }
  }

  return (
    <div className="card">
      <h2>Payment policy</h2>
      <p className="muted small">
        Thresholds the rules and reviewers work to: date window, receiving accounts, per-currency second-approver
        limits, manual-credit role, pilot channels. Auto-approval cannot be enabled in this release.
      </p>
      <p>Active version <code>{current.data?.version}</code></p>
      <textarea rows={12} value={text} readOnly={draft === null} onChange={(e) => setDraft(e.target.value)} spellCheck={false} aria-label="Policy configuration (JSON)" />

      {draft !== null && (
        <div className="diff">
          <h3>Changes vs active version</h3>
          {diffError && <p className="error small">Invalid JSON: {diffError}</p>}
          {!diffError && diff.length === 0 && <p className="muted small">No changes yet.</p>}
          {diff.map((d) => (
            <div key={d.key} className={`diff-row ${d.kind}`}>
              <span className="badge">{d.kind}</span> <code>{d.key}</code>
              {d.kind === "changed" && (
                <span className="mono small">
                  {JSON.stringify(d.before)} → {JSON.stringify(d.after)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      <form className="inline" onSubmit={(e) => { e.preventDefault(); publish.mutate(); }}>
        {draft !== null ? (
          <>
            <input placeholder="New version name, e.g. 2026.11-pilot" value={version} onChange={(e) => setVersion(e.target.value)} />
            <button disabled={!version || diffError !== null || diff.length === 0 || publish.isPending}>Publish new version</button>
            <button type="button" className="ghost" onClick={() => setDraft(null)}>Discard edits</button>
          </>
        ) : (
          <button type="button" className="ghost" onClick={() => setDraft(text)}>
            Edit policy
          </button>
        )}
      </form>
      {retire.error && <p className="error">{(retire.error as Error).message}</p>}
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
              <td>{v.active && <button className="ghost small" onClick={() => retire.mutate(v.policy_id)}>Retire</button>}</td>
            </tr>
          ))}
          {history.data?.length === 0 && <tr><td colSpan={5} className="muted">Using the built-in default policy.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

// ── shadow-mode scorecard: advisory AI vs human decisions ───────────────────────────
function pct(v: number): string {
  return `${(v * 100).toFixed(0)}%`;
}

function rateBadge(v: number, invert: boolean): string {
  // for error rates low is good; for agreement high is good
  const good = invert ? v <= 0.05 : v >= 0.9;
  const bad = invert ? v > 0.2 : v < 0.7;
  return `badge ${good ? "tier-green" : bad ? "tier-red" : "tier-amber"}`;
}

function ShadowPanel() {
  const card = useQuery({ queryKey: ["shadow-scorecard"], queryFn: () => api<ShadowScorecard>("/shadow/scorecard?days=30") });
  if (card.isError) return null; // reviewers without access simply see nothing
  return (
    <div className="card">
      <div className="toolbar">
        <h2>Shadow scorecard</h2>
        <span className={`badge ${card.data?.enabled ? "tier-green" : ""}`}>
          {card.data ? (card.data.enabled ? "recording" : "off") : "…"}
        </span>
      </div>
      <p className="muted small">
        Advisory AI proposals measured against what reviewers actually decided. Proposals never touch a
        claim; automation only goes live when these numbers stay inside agreed limits for four straight weeks.
      </p>
      {card.isFetching && <p className="muted small">Loading…</p>}
      {card.data && card.data.proposals === 0 && (
        <p className="muted small">
          No proposals yet. Set <code>POPS_SHADOW_MODE_ENABLED=true</code> to start recording them.
        </p>
      )}
      {card.data && card.data.proposals > 0 && (
        <>
          <p className="muted small">
            Last {card.data.window_days} days · {card.data.proposals} proposals · {card.data.scored} scored
            against a reviewer decision.
          </p>
          <table>
            <thead>
              <tr>
                <th>Proposer</th>
                <th>Proposals</th>
                <th>Scored</th>
                <th>Agreement</th>
                <th>False approvals</th>
                <th>False holds</th>
              </tr>
            </thead>
            <tbody>
              {card.data.proposers.map((p) => (
                <tr key={p.proposer}>
                  <td><code>{p.proposer}</code></td>
                  <td>{p.proposals}</td>
                  <td>{p.scored}</td>
                  <td><span className={rateBadge(p.agreement_rate, false)}>{pct(p.agreement_rate)}</span></td>
                  <td>
                    <span className={rateBadge(p.false_approval_rate, true)}>
                      {pct(p.false_approval_rate)} ({p.false_approvals})
                    </span>
                  </td>
                  <td>
                    <span className={rateBadge(p.false_hold_rate, true)}>
                      {pct(p.false_hold_rate)} ({p.false_holds})
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export default function Guardrails() {
  const qc = useQueryClient();
  const toast = useToast();
  const admin = hasRole("admin");
  const [showRemoved, setShowRemoved] = useState(false);
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["guardrails", showRemoved], queryFn: () => api<Listing>(`/admin/guardrails?include_removed=${showRemoved}`) });
  const templates = useQuery({ queryKey: ["guardrail-templates"], queryFn: () => api<Template[]>("/admin/guardrails/templates") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["guardrails"] });
  const addTemplate = useMutation({
    mutationFn: (t: Template) => post("/admin/guardrails", t),
    onSuccess: (_d, t) => {
      toast("ok", `Guardrail "${t.name}" added.`);
      refresh();
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const toggle = useMutation({
    mutationFn: (r: Rule) => post(`/admin/guardrails/${r.rule_id}/enabled`, { enabled: !r.enabled }),
    onSuccess: (_d, r) => {
      toast("info", `"${r.name}" ${r.enabled ? "disabled" : "enabled"}.`);
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => del(`/admin/guardrails/${id}`, { reason }),
    onSuccess: () => {
      toast("info", "Guardrail removed (kept in the audit log).");
      refresh();
    },
  });
  const err = (addTemplate.error ?? toggle.error ?? remove.error) as Error | null;
  const active = new Set(list.data?.rules.filter((r) => !r.removed_at).map((r) => r.name));
  const reader = list.data?.reader;

  const addKillSwitch = (t: Template) => {
    if (t.kind === "ai_kill_switch" && !window.confirm("Switch the AI reader OFF? Claims will be read by OCR only and always go to review."))
      return;
    addTemplate.mutate(t);
  };

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
                  <span className="lock-chip" aria-hidden>🔒</span> <strong>{g.name}</strong>
                  <span className="badge tier-green locked-badge">always on</span>
                  <div className="muted small">{g.description}</div>
                </li>
              ))}
            </ul>
          </div>

          <div className="card">
            <div className="toolbar">
              <h2>Your guardrails</h2>
              <label className="small check"><input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} /> show removed</label>
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
                <div className="rule-foot">
                  <button
                    type="button"
                    className="ghost small"
                    onClick={() => setHistoryFor(historyFor === r.rule_id ? null : r.rule_id)}
                  >
                    {historyFor === r.rule_id ? "Hide history" : "History"}
                  </button>
                  {historyFor === r.rule_id && <AuditTimeline ruleId={r.rule_id} />}
                </div>
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
              <div key={t.name} className={`rule ${t.kind === "ai_kill_switch" ? "kill" : ""}`}>
                <div className="row">
                  <strong>{t.name}</strong>
                  <span className="badge">{ACTION_LABEL[t.action]}</span>
                  {admin && (
                    <span className="actions-right">
                      {t.kind === "ai_kill_switch" ? (
                        <button className="danger small" disabled={active.has(t.name) || addTemplate.isPending} onClick={() => addKillSwitch(t)}>
                          {active.has(t.name) ? "Added" : "Switch AI off"}
                        </button>
                      ) : (
                        <button className="small" disabled={active.has(t.name) || addTemplate.isPending} onClick={() => addKillSwitch(t)}>
                          {active.has(t.name) ? "Added" : "Add"}
                        </button>
                      )}
                    </span>
                  )}
                </div>
                <div className="muted small">{t.description}</div>
              </div>
            ))}
          </div>
          <PolicyPanel />
          <ShadowPanel />
        </div>
      </div>
    </section>
  );
}
