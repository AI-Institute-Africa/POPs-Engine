// Plain-language names for states and reason codes. The server speaks in codes; people should not have to.

export type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

export const STATE: Record<string, { label: string; tone: Tone; hint: string }> = {
  // payments
  RECEIVED: { label: "Received", tone: "neutral", hint: "Just arrived." },
  PROCESSING: { label: "Checking", tone: "info", hint: "The readers and rules are running." },
  NEEDS_INFORMATION: { label: "Needs a retake", tone: "warn", hint: "The person was asked for a clearer photo." },
  DOCUMENT_VERIFIED: { label: "Document read", tone: "info", hint: "The proof reads correctly." },
  AWAITING_FINANCIAL_CONFIRMATION: { label: "Waiting for the money", tone: "info", hint: "A receipt alone is never proof." },
  FINANCIALLY_CONFIRMED: { label: "Money confirmed", tone: "ok", hint: "Matched to a real credit; ready for a decision." },
  PARTIALLY_ALLOCATED: { label: "Part-paid", tone: "info", hint: "Part of the invoice is paid." },
  PENDING_REVIEW: { label: "Needs a person", tone: "warn", hint: "A reviewer has to look at this." },
  APPROVED: { label: "Approved", tone: "ok", hint: "The invoice is paid." },
  REJECTED: { label: "Rejected", tone: "bad", hint: "A reviewer declined it." },
  DUPLICATE_HOLD: { label: "Held: possible duplicate", tone: "bad", hint: "The same proof was used before." },
  REVERSED_OR_REFUNDED: { label: "Reversed", tone: "neutral", hint: "The allocation was put back." },
  // identity
  SUBMITTED: { label: "Submitted", tone: "neutral", hint: "Just arrived." },
  NEEDS_REVIEW: { label: "Needs a person", tone: "warn", hint: "A reviewer decides." },
  VERIFIED: { label: "Verified", tone: "ok", hint: "Identity confirmed." },
  EXPIRED: { label: "Expired", tone: "neutral", hint: "The verification is out of date." },
};

export function stateInfo(state: string) {
  return STATE[state] ?? { label: humanize(state), tone: "neutral" as Tone, hint: "" };
}

export function humanize(code: string): string {
  const s = code.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// What each reason code means, for the people who have to act on it.
export const REASONS: Record<string, string> = {
  // payments: outcomes
  AUTO_APPROVED: "Approved by the rules: money confirmed, exact payment, under the cap, nothing suspicious.",
  AWAITING_CREDIT: "No matching bank record yet, so the claim waits for the money.",
  SUSPECTED_FAKE: "Risk signals suggest the receipt may be fake. An administrator was alerted.",
  DUPLICATE_FILE: "This exact file was already submitted.",
  DUPLICATE_NEAR_FILE: "A near-identical image was already submitted.",
  DUPLICATE_REFERENCE: "This transaction reference was already used.",
  REFERENCE_ALREADY_CONSUMED: "This transaction has already been credited to an invoice.",
  MANY_LOW_CONFIDENCE_FIELDS: "Several fields were unagreed or hard to read, over the policy limit.",
  CAPTURE_BLURRY: "The photo is too blurry to read.",
  CAPTURE_TOO_SMALL: "The photo is too small to read.",
  CAPTURE_GLARE: "There is glare on the document.",
  AMOUNT_MISMATCH: "The amount on the receipt differs from the amount received.",
  WRONG_BENEFICIARY: "The payment went to an account that is not ours.",
  PARTIAL_PAYMENT: "Only part of the invoice was paid.",
  OVERPAYMENT: "More than the invoice amount was paid.",
  NOT_EXACT_PAYMENT: "Only exact payments are approved automatically.",
  NOT_GREEN: "A hard gate or flag stopped automatic approval.",
  OVER_CAP: "Above the automatic-approval limit.",
  // identity: outcomes
  AUTO_VERIFIED: "Verified by the rules: documents agree, face matches, selfie looks live, nothing reused.",
  REVIEWER_VERIFIED: "A reviewer verified this.",
  FACE_SCORE_LOW: "The face on the ID does not match the selfie closely enough.",
  FACE_UNAVAILABLE: "No face model was available, so a person decides.",
  LIVENESS_LOW: "The selfie does not look like a live capture.",
  DOC_EXPIRED: "The document has expired.",
  DOC_EXPIRES_SOON: "The document expires very soon.",
  FIELD_CONFLICT: "Two sources disagree on a field, for example the printed expiry versus the machine-readable zone.",
  LOW_OCR_AGREEMENT: "The readers did not agree on the key fields.",
  MRZ_CHECKSUM_FAIL: "The machine-readable zone failed its check digits, so it was altered or misread.",
  MRZ_REQUIRED: "A passport must carry a valid machine-readable zone.",
  ID_NUMBER_USED_BY_OTHER_SUBJECT: "This ID number is already tied to a different person.",
  IMAGE_REUSED_BY_OTHER_SUBJECT: "These photos were already used by a different person.",
  SELFIE_COPIED_FROM_ID_PHOTO: "The selfie looks like a copy of the photo printed on the ID.",
  NO_FACE_IN_SELFIE: "No face could be found in the selfie.",
  NO_FACE_ON_ID: "No portrait could be found on the document.",
  COUNTRY_NOT_ALLOWED: "This country is not accepted for automatic verification.",
  DOC_TYPE_NOT_ALLOWED: "This document type is not accepted for automatic verification.",
  NUMBER_FORMAT_INVALID: "The ID number does not match the country's format.",
  DOC_TAMPER_SUSPECTED: "The image shows signs of editing.",
  READERS_UNAVAILABLE: "The readers were unavailable, so a person decides.",
  DISABLED: "Automatic verification is switched off for this organisation.",
  KILL_SWITCH: "Automatic verification is switched off globally.",
  PIPELINE_FAILED: "Processing failed repeatedly, so a person takes over.",
  KYC_NOT_VERIFIED: "The payer has no valid identity check on file.",
};

export function reasonText(code: string): string {
  return REASONS[code] ?? humanize(code);
}

// Names for the checks the identity pipeline runs.
export const CHECKS: Record<string, string> = {
  capture_front: "Photo of the ID is clear enough",
  capture_selfie: "Selfie is clear enough",
  card_detection: "Found the card in the photo",
  face_match: "Face on the ID compared with the selfie",
  liveness: "Selfie looks like a live capture",
  readers: "Document read by independent readers",
  mrz: "Machine-readable zone check digits",
  agreement: "Readers agree on the key fields",
  tamper: "No signs of image editing",
  integrity: "Not reused, not a duplicate",
};

export const FIELD_LABELS: Record<string, string> = {
  full_name: "Full name",
  surname: "Surname",
  given_names: "Given names",
  document_number: "Document number",
  date_of_birth: "Date of birth",
  expiry_date: "Expiry date",
  issuing_country: "Issuing country",
  doc_type: "Document type",
  sex: "Sex",
  nationality: "Nationality",
};

export const DOC_TYPES: Record<string, string> = {
  national_id: "National ID card",
  passport: "Passport",
  drivers_licence: "Driving licence",
};
