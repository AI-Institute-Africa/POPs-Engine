// What a payer is told for each reviewer action. Kept in step with _payer_text() in
// backend/app/claims/service.py: a rejection never says why and never hints at suspicion.
// The WhatsApp outbox sends these as-is; web payers see the matching tracking-page panel.

export const REJECT_PAYER_MESSAGE = "We could not confirm your payment. Please contact our finance team.";

export const REJECT_CONSEQUENCES = [
  "The claim is closed and cannot be confirmed afterwards.",
  "The payer is told only the message above, never the reason you pick.",
  "Your reason and note are kept in the audit trail.",
  "The payer can send a new proof for the same invoice.",
];

export const REQUEST_INFO_NOTE = "Your message is sent to the payer exactly as you type it. Do not mention suspicion or fraud.";
