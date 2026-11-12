import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, money, Obligation, post } from "../api";
import { Chip, EmptyState, Loading, PageHeader } from "../ui";
import "./review.css";

type Balance = { paid_minor: number; outstanding_minor: number; currency: string; payment_status: string };

function BalanceCells({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["balance", id], queryFn: () => api<Balance>(`/obligations/${id}/balance`) });
  if (!q.data) return <><td className="num">…</td><td className="num">…</td><td /></>;
  return (
    <>
      <td className="num">{money(q.data.paid_minor, q.data.currency)}</td>
      <td className="num">{money(q.data.outstanding_minor, q.data.currency)}</td>
      <td>
        <Chip tone={q.data.payment_status === "paid" ? "ok" : q.data.payment_status === "partially_paid" ? "info" : "neutral"}>
          {q.data.payment_status === "paid" ? "Paid" : q.data.payment_status === "partially_paid" ? "Part-paid" : q.data.payment_status === "unpaid" ? "Unpaid" : q.data.payment_status.replace(/_/g, " ")}
        </Chip>
      </td>
    </>
  );
}

// Normally the host system creates obligations via the API; this page exists for pilots and support.
export default function Obligations() {
  const qc = useQueryClient();
  const [learner, setLearner] = useState("");
  const [amount, setAmount] = useState("40.00");
  const [currency, setCurrency] = useState("USD");
  const [description, setDescription] = useState("Course instalment");
  const [externalRef, setExternalRef] = useState("");
  const [dueDate, setDueDate] = useState("");
  const list = useQuery({ queryKey: ["obligations"], queryFn: () => api<Obligation[]>("/obligations") });
  const create = useMutation({
    mutationFn: () =>
      post<Obligation>("/obligations", {
        learner_ref: learner,
        type: "instalment",
        amount_minor: Math.round(Number(amount) * 100),
        currency,
        description,
        external_ref: externalRef || null,
        due_date: dueDate || null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["obligations"] }),
  });

  return (
    <section className="rv">
      <PageHeader
        title="Invoices"
        sub="What payers owe. Normally your own system creates these through the API; this page is for pilots and support."
      />
      <div className="card">
        <h2>New invoice</h2>
        <p className="rv-help">Each invoice gets a payment code. Ask the payer to put it in the payment reference so their money can be matched.</p>
        <form className="inline" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <input placeholder="Learner ref" aria-label="Learner ref" value={learner} onChange={(e) => setLearner(e.target.value)} />
          <input placeholder="Description" aria-label="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
          <input placeholder="Your reference (optional)" aria-label="Your reference" value={externalRef} onChange={(e) => setExternalRef(e.target.value)} />
          <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} aria-label="Due date" />
          <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} aria-label="Amount" />
          <select value={currency} onChange={(e) => setCurrency(e.target.value)} aria-label="Currency">
            <option>USD</option>
            <option>ZWG</option>
          </select>
          <button disabled={!learner || create.isPending}>Create</button>
        </form>
        {create.error && <p className="error">{(create.error as Error).message}</p>}
        {create.data && <p className="flash">Payment code <code>{create.data.payment_code}</code> — ask the payer to put it in the payment reference.</p>}
      </div>
      {list.isLoading && <Loading lines={4} />}
      {list.error && <p className="error">{(list.error as Error).message}</p>}
      {list.data?.length === 0 && (
        <EmptyState icon="📄" title="No invoices yet">Create one above, or run a story from Home to fill this.</EmptyState>
      )}
      {!!list.data?.length && (
        <div className="card rv-flush">
          <div className="table-wrap">
            <table>
              <thead><tr><th>Created</th><th>Learner</th><th>Description</th><th className="num">Amount</th><th className="num">Paid</th><th className="num">Outstanding</th><th>Status</th><th>Due</th><th>Payment code</th></tr></thead>
              <tbody>
                {list.data.map((o) => (
                  <tr key={o.obligation_id}>
                    <td>{new Date(o.created_at).toLocaleDateString()}</td>
                    <td>{o.learner_ref}</td>
                    <td>{o.description}</td>
                    <td className="num">{money(o.amount_minor, o.currency)}</td>
                    <BalanceCells id={o.obligation_id} />
                    <td>{o.due_date ? new Date(o.due_date).toLocaleDateString() : ""}</td>
                    <td><code>{o.payment_code}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
