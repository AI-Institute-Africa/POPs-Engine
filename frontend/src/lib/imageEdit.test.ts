import { describe, expect, it } from "vitest";
import { cropRect, NO_TRIM } from "./imageEdit";

describe("cropRect", () => {
  it("keeps the whole image with no trim", () => expect(cropRect(1000, 800, NO_TRIM)).toEqual({ x: 0, y: 0, w: 1000, h: 800 }));
  it("trims each edge by a percentage", () => {
    expect(cropRect(1000, 800, { top: 10, right: 20, bottom: 5, left: 10 })).toEqual({ x: 100, y: 80, w: 700, h: 680 });
  });
  it("clamps wild values and never collapses the image", () => {
    const r = cropRect(1000, 800, { top: 99, right: 99, bottom: 99, left: 99 });
    expect(r.w).toBeGreaterThanOrEqual(100);
    expect(r.h).toBeGreaterThanOrEqual(80);
    expect(cropRect(1000, 800, { top: -5, right: NaN, bottom: 0, left: 0 })).toEqual({ x: 0, y: 0, w: 1000, h: 800 });
  });
});
