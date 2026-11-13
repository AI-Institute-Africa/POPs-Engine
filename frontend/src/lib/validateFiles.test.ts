import { describe, expect, it } from "vitest";
import { MAX_BYTES, MAX_FILES, MAX_TEXT, validateFiles } from "./validateFiles";

const f = (name: string, size = 1000, type = "image/png") => ({ name, size, type });

describe("validateFiles", () => {
  it("accepts supported files", () => {
    expect(validateFiles([], [f("a.png"), f("b.pdf", 10, "application/pdf")]).errors).toEqual([]);
  });
  it("skips duplicates by name and size", () => {
    const a = f("a.png");
    expect(validateFiles([a], [f("a.png")]).files).toHaveLength(1);
  });
  it("rejects oversize, empty and unsupported files", () => {
    const r = validateFiles([], [f("big.png", MAX_BYTES + 1), f("e.png", 0), f("x.exe", 5, "application/x-msdownload")]);
    expect(r.files).toHaveLength(0);
    expect(r.errors).toHaveLength(3);
  });
  it("caps the number of files", () => {
    const many = Array.from({ length: MAX_FILES + 2 }, (_, i) => f(`p${i}.png`, 100 + i));
    const r = validateFiles([], many);
    expect(r.files).toHaveLength(MAX_FILES);
    expect(r.errors).toHaveLength(2);
  });
  it("limits pasted text", () => {
    expect(validateFiles([], [], "x".repeat(MAX_TEXT + 1)).errors).toHaveLength(1);
  });
  it("accepts HEIC with an empty mime type by extension", () => {
    expect(validateFiles([], [f("IMG_1.HEIC", 50, "")]).files).toHaveLength(1);
  });
});
