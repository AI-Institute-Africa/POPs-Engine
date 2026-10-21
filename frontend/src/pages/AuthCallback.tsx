import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { completeOidcLogin } from "../oidc";

export default function AuthCallback() {
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    completeOidcLogin()
      .then(() => nav("/queue", { replace: true }))
      .catch((e: Error) => setErr(e.message));
  }, [nav]);
  return <p className={err ? "error" : "muted"}>{err ?? "Signing you in…"}</p>;
}
