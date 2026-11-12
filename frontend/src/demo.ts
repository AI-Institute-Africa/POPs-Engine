// Client for the standalone demo's /demo endpoints (only present when the server runs in demo mode).
import { API, api, post } from "./api";

export type ScenarioInfo = {
  id: string;
  group: "payments" | "identity";
  title: string;
  pitch: string;
  learn: string;
  outcome: string;
};

export type DemoInfo = {
  demo: true;
  org: string;
  scenarios: ScenarioInfo[];
  simulated: { readers: boolean; face: boolean };
};

export type Followup = { id: string; label: string; params: { claim_id: string; txn: string; amount: string } };

export type ScenarioResult = {
  scenario: string;
  kind: "payment" | "kyc";
  ref_id: string;
  state: string;
  headline: string;
  reasons: string[];
  followups: Followup[];
  payer_messages: string[];
  steps: { who: string; text: string }[];
};

export type PaymentSample = {
  variant: string;
  note: string;
  payment_code: string;
  claimant_ref: string;
  amount: string;
  currency: string;
  channel: string;
  file_url: string;
  filename: string;
};

export type KycSampleOut = { variant: string; note: string; subject_ref: string; front_url: string; selfie_url: string };

let info: DemoInfo | null = null;

export async function loadDemo(): Promise<DemoInfo | null> {
  try {
    info = await api<DemoInfo>("/demo/info");
  } catch {
    info = null;
  }
  return info;
}

export const demoInfo = () => info;
export const isDemo = () => info !== null;

export const runScenario = (id: string) => post<ScenarioResult>(`/demo/scenarios/${id}/run`, {});
export const deliverMoney = (p: Followup["params"]) => post<ScenarioResult>("/demo/followups/deliver-money", p);
export const preparePayment = (variant: string) => post<PaymentSample>(`/demo/prepare/payment/${variant}`, {});
export const prepareKyc = (variant: string) => post<KycSampleOut>(`/demo/prepare/kyc/${variant}`, {});
export const resetDemo = () => post<{ seeded: Record<string, number> }>("/demo/reset", {});

// A generated sample as a File the upload inputs can use.
export async function sampleFile(url: string, name: string): Promise<File> {
  const res = await fetch(`${API}${url}`);
  if (!res.ok) throw new Error("That sample has expired. Create a new one.");
  return new File([await res.blob()], name, { type: "image/png" });
}
