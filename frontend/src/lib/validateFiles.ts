// Client-side pre-flight for proof uploads. The server enforces the same limits; this only
// saves a round trip. Limits mirror docs/configuration.md.

export const MAX_FILES = 5;
export const MAX_BYTES = 10 * 1024 * 1024;
export const MAX_TEXT = 20_000;

const ALLOWED_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/heic",
  "image/heif",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);
const ALLOWED_EXT = /\.(png|jpe?g|webp|heic|heif|pdf|docx)$/i;

export type FileLike = { name: string; size: number; type: string };

export function fileKey(f: FileLike): string {
  return `${f.name}:${f.size}`;
}

/** Drop files already in the list (same name and size), then check count, size and type. */
export function validateFiles<T extends FileLike>(existing: T[], incoming: T[], text = ""): { files: T[]; errors: string[] } {
  const errors: string[] = [];
  const seen = new Set(existing.map(fileKey));
  const files = [...existing];
  for (const f of incoming) {
    if (seen.has(fileKey(f))) continue;
    if (!ALLOWED_TYPES.has(f.type) && !ALLOWED_EXT.test(f.name)) {
      errors.push(`${f.name}: this file type is not supported. Use a photo, PDF or Word file.`);
      continue;
    }
    if (f.size > MAX_BYTES) {
      errors.push(`${f.name}: larger than ${MAX_BYTES / 1024 / 1024} MB.`);
      continue;
    }
    if (f.size === 0) {
      errors.push(`${f.name}: the file is empty.`);
      continue;
    }
    if (files.length >= MAX_FILES) {
      errors.push(`You can add up to ${MAX_FILES} files. ${f.name} was not added.`);
      continue;
    }
    seen.add(fileKey(f));
    files.push(f);
  }
  if (text.length > MAX_TEXT) errors.push(`Pasted text is longer than ${MAX_TEXT.toLocaleString()} characters.`);
  return { files, errors };
}
