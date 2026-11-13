import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, money, type Claim } from "../api";
import { ClaimTimeline } from "../components/ClaimTimeline";
import { ProvisionalBanner } from "../components/ProvisionalBanner";
import { useI18n } from "../i18n";
import { claimProgress } from "../lib/claimSteps";
import { Loading, PageHeader } from "../ui";
import "./track.css";

/** What a payer sees for one claim: where it is, what happens next, and never why a risk flag fired. */
export default function Track() {
  const { id = "" } = useParams();
  const { t } = useI18n();
  const q = useQuery({
    queryKey: ["track", id],
    queryFn: () => api<Claim>(`/claims/${id}`),
    refetchInterval: (query) => claimProgress(query.state.data?.state).pollMs,
  });

  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data) {
    return (
      <div className="card">
        <p>{t("track.notfound")}</p>
        <Link to="/verify">{t("track.back")}</Link>
      </div>
    );
  }
  const claim = q.data;
  const p = claimProgress(claim.state);
  return (
    <div className="track">
      <PageHeader title={t("track.title")} sub={<>Claim <code>{claim.claim_id.slice(0, 8)}</code></>} />
      <ProvisionalBanner obligationId={claim.obligation_id} />
      <div className="card">
        <ClaimTimeline state={claim.state} />
        <div className={`next-panel tone-${p.tone}`} role="status" aria-live="polite">
          <h2>{t(`panel.${p.panel}.title`)}</h2>
          <p>{t(`panel.${p.panel}.body`)}</p>
          <div className="next-actions">
            {p.panel === "needs_info" && <Link className="btn" to="/verify">{t("track.addproof")}</Link>}
            {p.panel === "rejected" && <Link className="btn" to="/verify">{t("track.newproof")}</Link>}
          </div>
        </div>
        {claim.declared_amount_minor !== null && (
          <p className="muted small">
            {t("result.declared")}: {money(claim.declared_amount_minor, claim.declared_currency)}
          </p>
        )}
      </div>
    </div>
  );
}
