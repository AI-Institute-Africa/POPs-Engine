import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, money, Obligation, post } from "../api";

// Normally Cognify creates obligations via the API; this page exists for pilots and support.
export default function Obligations() {
  const qc = useQueryClient();
  const [learner, setLearner] = useState("");
  const [amount, setAmount] = useState("40.00");
  const [currency, setCurrency] = useState("USD");
  const [description, setDescription] = useState("Course instalment");
  const list = useQuery({ queryKey: ["obligations"], queryFn: () => api<Obligation[]>("/obligations") });
  const create = useMutation({
    mutationFn: () =>
      post<Obligation>("/obligations", {
        learner_ref: learner,
        type: "instalment",
        amount_minor: Math.round(Number(amount) * 100),
        currency,
        description,
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
        <thead><tr><th>Created</th><th>Learner</th><th>Description</th><th>Amount</th><th>Payment code</th></tr></thead>
        <tbody>
          {list.data?.map((o) => (
            <tr key={o.obligation_id}>
              <td>{new Date(o.created_at).toLocaleDateString()}</td>
              <td>{o.learner_ref}</td>
              <td>{o.description}</td>
              <td>{money(o.amount_minor, o.currency)}</td>
              <td><code>{o.payment_code}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
