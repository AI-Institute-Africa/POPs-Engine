// The four demo personas: one per role, so a viewer can step into each seat. The server is
// authoritative for permissions; this only decides what the console shows and who we log in as.

export type PersonaKey = "payer" | "reviewer" | "approver" | "admin";

export type Persona = {
  key: PersonaKey;
  label: string;
  who: string;
  role: "submitter" | "reviewer" | "approver" | "admin";
  email: string;
  blurb: string;
  color: string;
  initials: string;
};

export const PERSONAS: Record<PersonaKey, Persona> = {
  payer: {
    key: "payer",
    label: "Payer",
    who: "Amara, a learner",
    role: "submitter",
    email: "amara.payer@demo.validate",
    blurb: "Pays fees and proves who they are",
    color: "#0891b2",
    initials: "AP",
  },
  reviewer: {
    key: "reviewer",
    label: "Finance reviewer",
    who: "Rudo",
    role: "reviewer",
    email: "rudo.reviewer@demo.validate",
    blurb: "Works the queue of cases that need a person",
    color: "#7c3aed",
    initials: "RR",
  },
  approver: {
    key: "approver",
    label: "Finance approver",
    who: "Kuda",
    role: "approver",
    email: "kuda.approver@demo.validate",
    blurb: "Second approvals, reversals, manual credits",
    color: "#d97706",
    initials: "KA",
  },
  admin: {
    key: "admin",
    label: "Administrator",
    who: "Tariro",
    role: "admin",
    email: "tariro.admin@demo.validate",
    blurb: "Sees everything, sets the rules",
    color: "#2563eb",
    initials: "TA",
  },
};

export const PERSONA_LIST = Object.values(PERSONAS);

export function personaForRole(role: string): Persona {
  return PERSONA_LIST.find((p) => p.role === role) ?? PERSONAS.admin;
}
