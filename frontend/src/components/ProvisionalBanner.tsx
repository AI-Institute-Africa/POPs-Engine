import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { timeLeft } from "../lib/countdown";
import { useI18n } from "../i18n";

type Access = { state: "confirmed" | "provisional" | "none"; expires_at?: string };

/** Tells a payer their temporary access is running and when it ends. Fails silently. */
export function ProvisionalBanner({ obligationId }: { obligationId: string }) {
  const { t } = useI18n();
  const q = useQuery({
    queryKey: ["access", obligationId],
    queryFn: () => api<Access>(`/obligations/${obligationId}/access`),
    refetchInterval: 60_000,
    retry: false,
  });
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const data = q.data;
  if (!data || data.state !== "provisional" || !data.expires_at) return null;
  const left = timeLeft(data.expires_at, now);
  if (!left) return null;
  return (
    <div className={`prov-banner ${left.urgent ? "urgent" : ""}`} role="status">
      <strong>{t("prov.title")}</strong> {t("prov.body")} <b>{left.label}</b>.
      {left.urgent && <span> {t("prov.urgent")}</span>}
    </div>
  );
}
