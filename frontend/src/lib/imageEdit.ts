// Client-side image fixes done before upload, so a sideways photo is corrected once instead of
// bouncing back as "unreadable". The edited copy replaces the original in the upload list.

export async function rotateImage(file: File, quarterTurns: 1 | 2 | 3 = 1): Promise<File> {
  const bitmap = await createImageBitmap(file);
  const swap = quarterTurns % 2 === 1;
  const canvas = document.createElement("canvas");
  canvas.width = swap ? bitmap.height : bitmap.width;
  canvas.height = swap ? bitmap.width : bitmap.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas unavailable");
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate((quarterTurns * Math.PI) / 2);
  ctx.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);
  bitmap.close();
  const type = file.type === "image/png" ? "image/png" : "image/jpeg";
  const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, type, 0.92));
  if (!blob) throw new Error("could not encode image");
  return new File([blob], file.name, { type, lastModified: Date.now() });
}

export type Trim = { top: number; right: number; bottom: number; left: number }; // percent 0-45

export const NO_TRIM: Trim = { top: 0, right: 0, bottom: 0, left: 0 };

const clampPct = (n: number) => Math.min(45, Math.max(0, Number.isFinite(n) ? n : 0));

/** Pixel rectangle left after trimming each edge by a percentage. Never smaller than 10% each way. */
export function cropRect(width: number, height: number, trim: Trim) {
  const t = { top: clampPct(trim.top), right: clampPct(trim.right), bottom: clampPct(trim.bottom), left: clampPct(trim.left) };
  const x = Math.round((width * t.left) / 100);
  const y = Math.round((height * t.top) / 100);
  const w = Math.max(Math.round(width * 0.1), Math.round(width - x - (width * t.right) / 100));
  const h = Math.max(Math.round(height * 0.1), Math.round(height - y - (height * t.bottom) / 100));
  return { x, y, w, h };
}

export async function cropImage(file: File, trim: Trim): Promise<File> {
  const bitmap = await createImageBitmap(file);
  const r = cropRect(bitmap.width, bitmap.height, trim);
  const canvas = document.createElement("canvas");
  canvas.width = r.w;
  canvas.height = r.h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas unavailable");
  ctx.drawImage(bitmap, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
  bitmap.close();
  const type = file.type === "image/png" ? "image/png" : "image/jpeg";
  const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, type, 0.92));
  if (!blob) throw new Error("could not encode image");
  return new File([blob], file.name, { type, lastModified: Date.now() });
}
