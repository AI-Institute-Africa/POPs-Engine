import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, hasRole } from "../api";
import { deliverMoney, demoInfo, runScenario, type ScenarioInfo, type ScenarioResult } from "../demo";
import { stateInfo } from "../labels";
import { Chip, InspectLink, Reasons, StateChip } from "../ui";

// The curated order for the guided tour; every other story stays available below it.
const TOUR = ["pay-clear", "pay-waiting", "pay-edited", "kyc-clear", "kyc-mismatch", "kyc-edited"];

const WHO: Record<string, { initials: string; label: string }> = {
  payer: { initials: "P", label: "Payer" },
  reviewer: { initials: "R", label: "Reviewer" },
  approver: { initials: "A", label: "Approver" },
  admin: { initials: "AD", label: "Administrator" },
  system: { initials: "V", label: "Validate" },
};

function Story({ steps }: { steps: ScenarioResult["steps"] }) {
  return (
    <ol className="story">
      {steps.map((s, i) => {
        const w = WHO[s.who] ?? WHO.system;
        return (
          <li key={i} style={{ animationDelay: `${i * 380}ms` }}>
            <span className={`who${s.who === "system" ? " sys" : ""}`} aria-hidden>
              {w.initials}
            </span>
            <span className="txt">
              <small>{w.label}</small>
              {s.text}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Outcome({ result, info, onFollow, following }: { result: ScenarioResult; info: ScenarioInfo; onFollow: (f: ScenarioResult["followups"][number]) => void; following: boolean }) {
  const tone = stateInfo(result.state).tone;
  const link =
    result.kind === "payment"
      ? { to: `/claims/${result.ref_id}`, label: "Open the case file" }
      : { to: `/kyc/review?open=${result.ref_id}`, label: result.state === "VERIFIED" ? "See it in the audit list" : "Open the identity check" };
  const toAudit = result.kind === "kyc" && result.state === "VERIFIED";
  return (
    <div className="outcome" aria-live="polite">
      <div className="head">
        <StateChip state={result.state} />
        <span className="headline">{result.headline}</span>
      </div>
      <Story steps={result.steps} />
      <div className="bubble-label">What the {result.kind === "payment" ? "payer" : "person"} is told</div>
      <div className="bubble">
        {result.payer_messages.length
          ? result.payer_messages.map((m, i) => <div key={i}>{m}</div>)
          : tone === "ok"
            ? result.kind === "payment"
              ? "Your payment is confirmed. Thank you."
              : "You are verified."
            : "Your submission is being checked. We will let you know."}
      </div>
      {result.reasons.length > 0 && (
        <>
          <div className="bubble-label">Why the product decided this</div>
          <Reasons codes={result.reasons} />
        </>
      )}
      <div className="learn">
        <b>The point:</b> {info.learn}
      </div>
      <div className="scn-actions">
        {result.followups.map((f) => (
          <button key={f.id} type="button" disabled={following} onClick={() => onFollow(f)}>
            {following ? "Importing…" : f.label}
          </button>
        ))}
        {toAudit ? (
          <InspectLink to="/kyc/review">{link.label}</InspectLink>
        ) : (
          <InspectLink to={link.to}>{link.label}</InspectLink>
        )}
        {result.scenario === "pay-edited" && <InspectLink to="/fraud">See the fraud alert</InspectLink>}
      </div>
    </div>
  );
}

export function ScenarioPanel({ info, compact = false, footer }: { info: ScenarioInfo; compact?: boolean; footer?: React.ReactNode }) {
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const run = useMutation({ mutationFn: () => runScenario(info.id), onSuccess: setResult });
  const follow = useMutation({ mutationFn: deliverMoney, onSuccess: setResult });
  return (
    <div className="card scn">
      <div className="scn-head">
        <div className="tag">{info.group === "payments" ? "Payments" : "Identity"}</div>
        <h2>{info.title}</h2>
        <p className="muted">{info.pitch}</p>
      </div>
      <div className="scn-body">
        {!result && (
          <div className="scn-actions">
            <button type="button" className="lg" disabled={run.isPending} onClick={() => run.mutate()}>
              {run.isPending ? "Running…" : "▶  Run this story"}
            </button>
            {run.isPending && <span className="muted small">Acting as the payer, then the system…</span>}
            {run.isError && <span className="error">{(run.error as Error).message}</span>}
          </div>
        )}
        {result && !compact && (
          <div className="scn-actions">
            <button type="button" className="secondary" disabled={run.isPending} onClick={() => { setResult(null); run.reset(); }}>
              ↺ Run it again
            </button>
          </div>
        )}
      </div>
      {result && <Outcome result={result} info={info} following={follow.isPending} onFollow={(f) => follow.mutate(f.params)} />}
      {follow.isError && <p className="error" style={{ margin: 12 }}>{(follow.error as Error).message}</p>}
      {footer}
    </div>
  );
}

function Stats() {
  const queue = useQuery({ queryKey: ["home-queue"], queryFn: () => api<unknown[]>("/review/queue"), enabled: hasRole("reviewer"), refetchInterval: 15000 });
  const kyc = useQuery({ queryKey: ["home-kyc"], queryFn: () => api<unknown[]>("/kyc/review/queue"), enabled: hasRole("reviewer"), refetchInterval: 15000 });
  const auto = useQuery({ queryKey: ["home-auto"], queryFn: () => api<{ summary: { approved: number } }>("/review/auto-approved"), enabled: hasRole("reviewer"), refetchInterval: 15000 });
  const autoKyc = useQuery({ queryKey: ["home-autokyc"], queryFn: () => api<{ summary: { verified: number } }>("/kyc/review/auto-verified"), enabled: hasRole("reviewer"), refetchInterval: 15000 });
  if (!hasRole("reviewer")) return null;
  const items = [
    { n: auto.data?.summary.approved, l: "payments approved by the rules" },
    { n: autoKyc.data?.summary.verified, l: "identities verified by the rules" },
    { n: queue.data?.length, l: "payments waiting for a person" },
    { n: kyc.data?.length, l: "identity checks waiting for a person" },
  ];
  return (
    <div className="stats" aria-label="Live numbers">
      {items.map((s) => (
        <div className="stat" key={s.l}>
          <div className="n">{s.n ?? "–"}</div>
          <div className="l">{s.l}</div>
        </div>
      ))}
    </div>
  );
}

export default function Home() {
  const demo = demoInfo();
  const [step, setStep] = useState(0);
  const [seen, setSeen] = useState<Set<number>>(new Set());
  const [group, setGroup] = useState<"all" | "payments" | "identity">("all");
  const [started, setStarted] = useState(false);
  const scenarios = demo?.scenarios ?? [];
  const byId = new Map(scenarios.map((s) => [s.id, s]));
  const tour = TOUR.map((id) => byId.get(id)).filter(Boolean) as ScenarioInfo[];
  const more = scenarios.filter((s) => !TOUR.includes(s.id) && (group === "all" || s.group === group));
  const current = tour[step];

  const go = (i: number) => {
    setStep(i);
    setSeen((s) => new Set(s).add(step));
    document.getElementById("tour")?.scrollIntoView({ block: "start" });
  };

  return (
    <div>
      <section className="hero2">
        <h1>Know that a payment, or a person, is real. In seconds.</h1>
        <p>
          Validate reads receipts and identity documents, checks them against the money and against each other, and
          approves the clear cases on its own. Anything doubtful goes to a person, with the reasons laid out.
        </p>
        <div className="cta">
          <button
            type="button"
            className="btn lg"
            onClick={() => {
              setStarted(true);
              setTimeout(() => document.getElementById("tour")?.scrollIntoView({ behavior: "smooth" }), 50);
            }}
          >
            ▶ Start the 3-minute tour
          </button>
          <Link className="btn ghost lg" to="/verify">Try it yourself</Link>
        </div>
        <div className="pillars">
          <div className="pillar"><b>1 · It reads</b><span>Two independent readers pull the facts from the picture.</span></div>
          <div className="pillar"><b>2 · It checks</b><span>The money, the rules and the documents must all agree.</span></div>
          <div className="pillar"><b>3 · A person decides when unsure</b><span>Nothing doubtful is approved or rejected by a machine.</span></div>
        </div>
      </section>

      <Stats />

      {demo ? (
        <>
          <div className="section-title" id="tour">
            <h2>Guided tour</h2>
            <span className="muted small">Six short stories. Each one runs the real product and shows what happened.</span>
          </div>
          {!started && (
            <div className="card">
              <p>
                You are signed in as the <b>administrator</b>, so every page is open to you. Use the menu at the top
                right to step into the payer's or the reviewer's seat at any time.
              </p>
              <button type="button" onClick={() => setStarted(true)}>Begin</button>
            </div>
          )}
          {started && current && (
            <div className="tour">
              <ol className="tour-steps" aria-label="Tour steps">
                {tour.map((t, i) => (
                  <li key={t.id}>
                    <button type="button" className={`tour-step${i === step ? " on" : ""}${seen.has(i) ? " done" : ""}`} onClick={() => go(i)} aria-current={i === step}>
                      <span className="num">{seen.has(i) ? "✓" : i + 1}</span>
                      <span>
                        {t.title}
                        <small>{t.group === "payments" ? "Payments" : "Identity"}</small>
                      </span>
                    </button>
                  </li>
                ))}
              </ol>
              <div>
                <ScenarioPanel key={current.id} info={current} />
                <div className="tour-nav">
                  <button type="button" className="secondary" disabled={step === 0} onClick={() => go(step - 1)}>← Back</button>
                  {step < tour.length - 1 ? (
                    <button type="button" onClick={() => go(step + 1)}>Next story →</button>
                  ) : (
                    <Link className="btn" to="/verify">Now try it yourself →</Link>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="section-title">
            <h2>More stories</h2>
            <div className="seg" role="tablist" aria-label="Filter stories">
              {(["all", "payments", "identity"] as const).map((g) => (
                <button key={g} type="button" role="tab" aria-selected={group === g} className={group === g ? "on" : ""} onClick={() => setGroup(g)}>
                  {g === "all" ? "All" : g === "payments" ? "Payments" : "Identity"}
                </button>
              ))}
            </div>
          </div>
          <div className="grid cols-2">
            {more.map((s) => (
              <ScenarioPanel key={s.id} info={s} />
            ))}
          </div>
        </>
      ) : (
        <div className="card">
          <h2>Welcome</h2>
          <p className="muted">This build has no demo scenarios. Use the menu to verify a payment or an identity document.</p>
        </div>
      )}

      <div className="section-title">
        <h2>How a decision is made</h2>
      </div>
      <div className="how">
        <div className="card">
          <h3>The picture is only a claim</h3>
          <p className="muted">A receipt can be edited in seconds, so on its own it never settles anything. The bank record, an alert from the merchant, or a checked document does.</p>
        </div>
        <div className="card">
          <h3>Fixed rules, written down</h3>
          <p className="muted">Automatic approval is a short list of checks anyone can read. It never rejects. If one fails, a person gets the case with the exact reason.</p>
        </div>
        <div className="card">
          <h3>Every step is on record</h3>
          <p className="muted">Decisions are append-only. A sample of automatic ones is checked by a person, and the overturn rate decides whether to widen the limits.</p>
        </div>
      </div>
      {demo && (
        <p className="muted small" style={{ marginTop: 18 }}>
          <Chip tone="info">Simulated</Chip> In this demo the vision readers and face matcher are simulated and only understand the sample
          documents the demo generates. In a real deployment they are a vision model and a face model you supply.
        </p>
      )}
    </div>
  );
}
