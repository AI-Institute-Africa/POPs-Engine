import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, money, Obligation, post } from "../api";

type Balance = { paid_minor: number; outstanding_minor: number; currency: string; payment_status: string };

function BalanceCells({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["balance", id], queryFn: () => api<Balance>(`/obligations/${id}/balance`) });
  if (!q.data) return <><td /><td /><td /></>;
  return (
    <>
      <td>{money(q.data.paid_minor, q.data.currency)}</td>
      <td>{money(q.data.outstanding_minor, q.data.currency)}</td>
      <td>{q.data.payment_status.replace("_", " ")}</td>
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
    <section>
      <div className="card">
        <h2>New obligation</h2>
        <form className="inline" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <input placeholder="Learner ref" value={learner} onChange={(e) => setLearner(e.target.value)} />
          <input placeholder="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
          <input placeholder="Your reference (optional)" value={externalRef} onChange={(e) => setExternalRef(e.target.value)} />
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
      <table>
        <thead><tr><th>Created</th><th>Learner</th><th>Description</th><th>Amount</th><th>Paid</th><th>Outstanding</th><th>Status</th><th>Due</th><th>Payment code</th></tr></thead>
        <tbody>
          {list.data?.map((o) => (
            <tr key={o.obligation_id}>
              <td>{new Date(o.created_at).toLocaleDateString()}</td>
              <td>{o.learner_ref}</td>
              <td>{o.description}</td>
              <td>{money(o.amount_minor, o.currency)}</td>
              <BalanceCells id={o.obligation_id} />
              <td>{o.due_date ? new Date(o.due_date).toLocaleDateString() : ""}</td>
              <td><code>{o.payment_code}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
