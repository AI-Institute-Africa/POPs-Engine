import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { post, setSession } from "../api";
import { oidcEnabled, userManager } from "../oidc";

// Development sign-in. Production uses Cognify identity (OIDC) with MFA for finance roles.
export default function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("reviewer@cognify.dev");
  const [role, setRole] = useState("reviewer");
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await post<{ access_token: string; role: string }>("/auth/dev-login", { email, role });
      setSession(r.access_token, r.role);
      nav("/queue");
    } catch (ex) {
      setErr((ex as Error).message);
    }
  }

  if (oidcEnabled) {
    return (
      <div className="login card">
        <h1>POPs Review Console</h1>
        <p className="muted">Sign in with your Cognify account. Finance roles need MFA.</p>
        <button onClick={() => void userManager!.signinRedirect()}>Sign in</button>
      </div>
    );
  }

  return (
    <form className="login card" onSubmit={submit}>
      <h1>POPs Review Console</h1>
      <p className="muted">Development sign-in</p>
      <label>
        Email
        <input value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label>
        Role
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="reviewer">Reviewer</option>
          <option value="approver">Approver</option>
          <option value="admin">Admin</option>
        </select>
      </label>
      {err && <p className="error">{err}</p>}
      <button type="submit">Sign in</button>
    </form>
  );
}
