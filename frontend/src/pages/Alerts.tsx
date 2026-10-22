import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, hasRole, money, post } from "../api";

type Source = {
  source_id: string;
  kind: string;
  name: string;
  provider: string;
  account_alias: string;
  allowed_senders: string[];
  require_dkim: boolean;
  active: boolean;
  last_seen_at: string | null;
  last_message_at: string | null;
  stale: boolean;
  key?: string;
};
type Message = {
  message_id: string;
  status: string;
  reason: string | null;
  sender: string;
  subject: string | null;
  body: string;
  received_at: string;
  parsed: { amount_minor?: number; currency?: string; reference?: string; payer?: string; template?: string } | null;
};

const STATUS_HELP: Record<string, string> = {
  credited: "became a credit",
  duplicate: "already known",
  ignored: "not an incoming payment",
  rejected: "untrusted sender or signature",
  unparsed: "no reference — not credited",
  conflict: "disagrees with a known credit",
};

function NewSource({ onCreated }: { onCreated: (s: Source) => void }) {
  const [kind, setKind] = useState("sms");
  const [name, setName] = useState("Merchant phone");
  const [provider, setProvider] = useState("ECOCASH");
  const [alias, setAlias] = useState("MAIN");
  const [senders, setSenders] = useState("EcoCash");
  const create = useMutation({
    mutationFn: () =>
      post<Source>("/alerts/sources", {
        kind,
        name,
        provider,
        account_alias: alias,
        allowed_senders: senders.split(",").map((s) => s.trim()).filter(Boolean),
      }),
    onSuccess: onCreated,
  });
  return (
    <form className="inline" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
      <select value={kind} onChange={(e) => { setKind(e.target.value); setSenders(e.target.value === "sms" ? "EcoCash" : "@bank.co.zw"); }} aria-label="Kind">
        <option value="sms">SMS forwarder</option>
        <option value="email">Alert mailbox</option>
      </select>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" />
      <input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="Provider (ECOCASH, CBZ…)" />
      <input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="Our account alias" />
      <input value={senders} onChange={(e) => setSenders(e.target.value)} placeholder={kind === "sms" ? "Allowed sender IDs, comma separated" : "Allowed addresses or @domains"} />
      <button disabled={create.isPending}>Register</button>
      {create.error && <span className="error">{(create.error as Error).message}</span>}
    </form>
  );
}

export default function Alerts() {
  const qc = useQueryClient();
  const admin = hasRole("admin");
  const [status, setStatus] = useState("");
  const [newKey, setNewKey] = useState<Source | null>(null);
  const sources = useQuery({ queryKey: ["alert-sources"], queryFn: () => api<Source[]>("/alerts/sources"), refetchInterval: 30000 });
  const messages = useQuery({
    queryKey: ["alert-messages", status],
    queryFn: () => api<Message[]>(`/alerts/messages${status ? `?status=${status}` : ""}`),
    refetchInterval: 15000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["alert-sources"] });
  const rotate = useMutation({ mutationFn: (id: string) => post<Source>(`/alerts/sources/${id}/rotate-key`, {}), onSuccess: (s) => { setNewKey(s); refresh(); } });
  const deactivate = useMutation({ mutationFn: (id: string) => post(`/alerts/sources/${id}/deactivate`, {}), onSuccess: refresh });

  return (
    <section>
      <div className="card">
        <h2>Payment alert sources</h2>
        <p className="muted small">
          Your own credit notifications — SMS on the merchant phone and alert emails from banks — confirm payments within seconds and are the strongest
          defence against fake receipts. Only registered sources, from allowed senders (and DKIM-signed for email), are accepted.
        </p>
        {newKey?.key && (
          <div className="flash">
            Key for <strong>{newKey.name}</strong> (shown once): <code className="key">{newKey.key}</code>
            <div className="small">
              {newKey.kind === "sms"
                ? <>Forwarder app: POST each SMS as JSON <code>{'{"from": "<sender>", "text": "<message>"}'}</code> to <code>/api/alerts/sms</code> with header <code>X-POPs-Source-Key</code>; send <code>/api/alerts/heartbeat</code> every few minutes.</>
                : <>Inbound-mail webhook: POST the raw email to <code>/api/alerts/email</code> with header <code>X-POPs-Source-Key</code>, or set <code>POPS_ALERT_IMAP_*</code> to poll a mailbox.</>}
            </div>
          </div>
        )}
        <table>
          <thead><tr><th>Source</th><th>Kind</th><th>Provider → account</th><th>Allowed senders</th><th>Last check-in</th><th></th></tr></thead>
          <tbody>
            {sources.data?.map((s) => (
              <tr key={s.source_id} className={s.active ? "" : "muted"}>
                <td><strong>{s.name}</strong></td>
                <td>{s.kind}{s.kind === "email" && s.require_dkim ? " · DKIM" : ""}</td>
                <td>{s.provider} → {s.account_alias}</td>
                <td className="small">{s.allowed_senders.join(", ") || "any"}</td>
                <td>
                  {!s.active ? "deactivated" : s.last_seen_at ? new Date(s.last_seen_at).toLocaleString() : "never"}
                  {s.active && s.stale && <span className="badge risk-high">silent</span>}
                </td>
                <td>
                  {admin && s.active && (
                    <span className="actions-right">
                      <button className="ghost small" onClick={() => rotate.mutate(s.source_id)}>Rotate key</button>
                      <button className="danger small" onClick={() => window.confirm(`Deactivate ${s.name}?`) && deactivate.mutate(s.source_id)}>Deactivate</button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
            {sources.data?.length === 0 && <tr><td colSpan={6} className="muted">No sources yet.</td></tr>}
          </tbody>
        </table>
        {admin && <NewSource onCreated={(s) => { setNewKey(s); refresh(); }} />}
      </div>

      <div className="card">
        <div className="toolbar">
          <h2>Alerts received</h2>
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">All</option>
            {Object.keys(STATUS_HELP).map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
        <table>
          <thead><tr><th>Received</th><th>Status</th><th>From</th><th>Read as</th><th>Message</th></tr></thead>
          <tbody>
            {messages.data?.map((m) => (
              <tr key={m.message_id}>
                <td className="small">{new Date(m.received_at).toLocaleString()}</td>
                <td>
                  <span className={`badge ${m.status === "credited" ? "tier-green" : m.status === "conflict" || m.status === "rejected" ? "risk-high" : ""}`}>{m.status}</span>
                  <div className="muted small">{m.reason ?? STATUS_HELP[m.status]}</div>
                </td>
                <td className="small">{m.sender}</td>
                <td className="small">
                  {m.parsed?.amount_minor != null && money(m.parsed.amount_minor, m.parsed.currency)} {m.parsed?.reference && <code>{m.parsed.reference}</code>}
                  {m.parsed?.payer && <div className="muted">{m.parsed.payer}</div>}
                </td>
                <td className="small reasons">{m.subject && <strong>{m.subject} — </strong>}{m.body}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
