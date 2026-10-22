// Tiny dictionary i18n for the payer-facing surface: English, Shona, Ndebele.
// Any key missing in a language falls back to English. Admin pages stay in English.
import { createContext, useContext, useState } from "react";

export type Lang = "en" | "sn" | "nd";

const KEY = "validate-lang";

const SN: Record<string, string> = {
  "nav.scan": "Skena",
  "nav.guardrails": "Guardrails",
  "hero.title": "Chenjedza kubhadhara",
  "hero.sub":
    "Tora foto, skena kana pawoora risiti yako yekubhadhara. Tinoverenga risiti, tobva taita cheki nemari yanga yakapihwa — risiti chete haizombobvunyi.",
  "drop.drag": "Ratsidza risiti pano",
  "drop.or": "kana sarudza nzira yekuuya nayo:",
  "capture.photo": "Tora foto",
  "capture.photo.sub": "inoshandisa kamera yako",
  "capture.scan": "Skena",
  "capture.scan.sub": "isa risiti mberi",
  "capture.upload": "Pawoora faira",
  "capture.upload.sub": "PNG, JPG kana PDF",
  "drop.ready": "kuti utumirwe",
  "form.title": "Mashoko ekubhadhara",
  "form.code": "Kodi yekubhadhara",
  "form.code.help": "Kodi yanotiwarenji kana pa checkout.",
  "form.code.nomatch": "Hakuna chinobhadhara chinopindirana nayo. Chenjedza kodi yako.",
  "form.name": "Zita rako kana nhamba yefoni",
  "form.name.ph": "senti tiwane nemari",
  "form.amount": "Mari yaunobhadhara",
  "form.amount.opt": "(yakaziviswa)",
  "form.ref": "Chiitiko chebanki",
  "form.ref.opt": "(chakaziviswa)",
  "form.submit": "Chenjedza kubhadhara kunoku",
  "form.submitting": "Kutumira…",
  "form.trust":
    "Risiti yako inonyengera. Inongoti chibvunzo chete — inochinjwa nemari yanga yanga yakapihwa chete.",
  "result.checking": "Tichi Chenjedza Risiti Yako…",
  "result.title": "Mhedzisiro",
  "result.status": "Mamiriro",
  "result.outcome": "Mhedzisiro",
  "result.declared": "Yakaziviswa",
  "result.channel": "Nzira",
  "result.green.note":
    "Timu ye-green inoreva risiti yakaverengwa zvakanaka uye yakawanikwa nemari yanga yanga yakapihwa — kubvumirana chaizvoizvo hakusi kwebhanki.",
  "steps.uploaded": "Yapawoorwa & yakachengetwa",
  "steps.quality": "Yakachenjedzwa kuti inoverengeka",
  "steps.reading": "Kutiverenga risiti",
  "steps.matching": "Kuionera nemari yabhanki",
  "tier.green": "Chiratidzo chakasimbiswa",
  "tier.amber": "Inoda kutarisirwa",
  "tier.red": "Hatari huru",
  "recent.title": "Kuongorora Kwazvino",
  "tips.title": "Kuti Zviverengwe Zvakanaka",
  "tips.1": "Torifoto yese risiti — pasina kucheka kana kungwarira kuti ina gunguwo.",
  "tips.2": "Isa nhamba yechiitiko uye mari mberi.",
  "tips.3": "Pawoora faira yekutanga pane kutora skrini shoti.",
  "footer.text": "Validate — demo isingashandiswe neganhundira. Risiti haisi chibvunzo, haisi chimbochido.",
};

const ND: Record<string, string> = {
  "nav.scan": "Skena",
  "nav.guardrails": "Guardrails",
  "hero.title": "Hlola ukukhokha",
  "hero.sub":
    "Thatha isithombe, skena noma layisha irekhodi lakho lokukhokha. Sifunda irekhodi, bese sihlola imali eyayingenile — irekhodi lodwa alikubeki.",
  "drop.drag": "Yehlisa irekhodi lapha",
  "drop.or": "noma khetha indlela yokulifaka:",
  "capture.photo": "Thatha isithombe",
  "capture.photo.sub": "kusetshenziswa ikhamera yakho",
  "capture.scan": "Skena",
  "capture.scan.sub": "beka irekhodi phambili",
  "capture.upload": "Layisha ifayela",
  "capture.upload.sub": "PNG, JPG noma i-PDF",
  "drop.ready": "ukuthumela",
  "form.title": "Imininingwane yokukhokha",
  "form.code": "Ikhodi yokukhokha",
  "form.code.help": "Ikhodi ekhombiswe e-invoiceni noma lapho uhlala khona.",
  "form.code.nomatch": "Ayikho imali ehambisana nale khodi. Hlola ikhodi yakho.",
  "form.name": "Igama lakho noma inombolo yakho yocingo",
  "form.name.ph": "sicinge nge imali",
  "form.amount": "Imali oyikhokhile",
  "form.amount.opt": "(ezisiwe)",
  "form.ref": "Ithuluzi lesikweletu lebhange",
  "form.ref.opt": "(elisiwe)",
  "form.submit": "Hlola lokhu kukhokha",
  "form.submitting": "Kuthunzelwa…",
  "form.trust":
    "Irekhodi lakho liqinisekiswa. Liyisicelo kuphela — liyaqinisekiswa ngemali engeniswe kuphela.",
  "result.checking": "Siya Hlola Irekhodi Lakho…",
  "result.title": "Umbhalo",
  "result.status": "Isimo",
  "result.outcome": "Ukuphendula",
  "result.declared": "Okusiwe",
  "result.channel": "Indlela",
  "result.green.note":
    "Umbala okuluhlaza ushoko lokuthi irekhodi lifundwe kahle futhi lihambisana ngenye imali — ukuvuma okugcwele kusengathi kubhange.",
  "steps.uploaded": "Kulayishwe & kuvikelekile",
  "steps.quality": "Kuhloliwe ngokufundeka",
  "steps.reading": "Ukufunda irekhodi",
  "steps.matching": "Ukuqhathanisa nemali yebhange",
  "tier.green": "Isikhombimandla esiqinisekisiwe",
  "tier.amber": "Kudingeka ucwaningo",
  "tier.red": "Ubungozi obukhulu",
  "recent.title": "Ukuhlola Kwakamuva",
  "tips.title": "Ukuze Kufundwe Kahle",
  "tips.1": "Thatha isithombe sonke sorekhodi — ungaphothi noma ushiye imiphetho.",
  "tips.2": "Beka inombolo yokukhokha nemali phambili.",
  "tips.3": "Layisha ifayela lokuqala kunokusika isithombe sesithombe.",
  "footer.text": "Validate — imodeli engathengisi. Irekhodi aliyisibopho, aliyisiqiniseko.",
};

const EN: Record<string, string> = {
  "nav.scan": "Scan",
  "nav.guardrails": "Guardrails",
  "hero.title": "Verify a payment",
  "hero.sub":
    "Take a photo, scan or upload your proof of payment. We read the receipt, then check it against real money received — the receipt alone never confirms anything.",
  "drop.drag": "Drag a receipt here",
  "drop.or": "or choose how to add it:",
  "capture.photo": "Take a photo",
  "capture.photo.sub": "uses your camera",
  "capture.scan": "Scan",
  "capture.scan.sub": "frame the receipt",
  "capture.upload": "Upload file",
  "capture.upload.sub": "PNG, JPG or PDF",
  "drop.ready": "ready to submit",
  "form.title": "Payment details",
  "form.code": "Payment code",
  "form.code.help": "The code shown on your invoice or at checkout.",
  "form.code.nomatch": "No obligation matches that code. Check the code on your invoice.",
  "form.name": "Your name or phone number",
  "form.name.ph": "so we can match you to the payment",
  "form.amount": "Amount you paid",
  "form.amount.opt": "(optional)",
  "form.ref": "Bank transaction reference",
  "form.ref.opt": "(optional)",
  "form.submit": "Validate this payment",
  "form.submitting": "Submitting…",
  "form.trust":
    "Your receipt is treated as untrusted evidence. It is only confirmed against money the recipient actually received.",
  "result.checking": "Checking your receipt…",
  "result.title": "Result",
  "result.status": "Status",
  "result.outcome": "Outcome",
  "result.declared": "Declared",
  "result.channel": "Channel",
  "result.green.note":
    "A green tier means the receipt reads cleanly and matched a real credit — final confirmation is still a finance decision.",
  "steps.uploaded": "Uploaded & malware-scanned",
  "steps.quality": "Quality & readability check",
  "steps.reading": "Reading the receipt",
  "steps.matching": "Matching against bank credits",
  "tier.green": "Confirmed signal",
  "tier.amber": "Needs a closer look",
  "tier.red": "High risk",
  "recent.title": "Recent checks",
  "tips.title": "For a clean read",
  "tips.1": "Photograph the whole receipt — no cropped edges or glare.",
  "tips.2": "Put the transaction reference and amount in view.",
  "tips.3": "Upload the original file rather than a screenshot of a screenshot.",
  "footer.text": "Validate — non-commercial demo. A receipt is a claim, not a confirmation.",
};

const DICTS: Record<Lang, Record<string, string>> = { en: EN, sn: SN, nd: ND };

export const LANG_LABELS: Record<Lang, string> = { en: "EN", sn: "Shona", nd: "Ndebele" };

type Ctx = { lang: Lang; setLang: (l: Lang) => void; t: (key: string) => string };

const I18nCtx = createContext<Ctx>({ lang: "en", setLang: () => {}, t: (k) => k });

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const v = localStorage.getItem(KEY);
    return v === "sn" || v === "nd" ? v : "en";
  });
  const setLang = (l: Lang) => {
    localStorage.setItem(KEY, l);
    setLangState(l);
  };
  const t = (key: string) => DICTS[lang][key] ?? EN[key] ?? key;
  return <I18nCtx.Provider value={{ lang, setLang, t }}>{children}</I18nCtx.Provider>;
}

export const useI18n = () => useContext(I18nCtx);
