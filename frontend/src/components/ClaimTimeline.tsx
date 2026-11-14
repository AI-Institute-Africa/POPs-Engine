import { claimProgress, type StepKey } from "../lib/claimSteps";
import { useI18n } from "../i18n";

const MARK = { done: "✓", failed: "!", current: "●", pending: "" } as const;

/** Six-step payer timeline. Collapses into a one-line summary once the claim is final. */
export function ClaimTimeline({ state }: { state: string }) {
  const { t } = useI18n();
  const p = claimProgress(state);
  const list = (
    <ol className="claim-steps" aria-label={t("track.timeline")}>
      {p.steps.map((s) => (
        <li key={s.key} className={s.status} aria-current={s.status === "current" ? "step" : undefined}>
          <span className="tl-mark" aria-hidden>
            {MARK[s.status]}
          </span>
          <span className="tl-label">{t(`step.${s.key as StepKey}`)}</span>
        </li>
      ))}
    </ol>
  );
  if (!p.final) return list;
  return (
    <details className="claim-steps-collapsed">
      <summary>{t("track.timeline")}</summary>
      {list}
    </details>
  );
}
