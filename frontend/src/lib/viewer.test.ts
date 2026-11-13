import { describe, expect, it } from "vitest";
import { cssFilter, DEFAULT_VIEW, isDefault, rotateBy, setLevel, zoomBy } from "./viewer";

describe("viewer state", () => {
  it("clamps zoom", () => {
    let v = DEFAULT_VIEW;
    for (let i = 0; i < 30; i++) v = zoomBy(v, 0.5);
    expect(v.zoom).toBe(4);
    for (let i = 0; i < 30; i++) v = zoomBy(v, -0.5);
    expect(v.zoom).toBe(0.5);
  });
  it("wraps rotation both ways", () => {
    expect(rotateBy(DEFAULT_VIEW, 270).rotate).toBe(270);
    expect(rotateBy(rotateBy(DEFAULT_VIEW, 270), 90).rotate).toBe(0);
    expect(rotateBy(DEFAULT_VIEW, -90).rotate).toBe(270);
  });
  it("clamps brightness and contrast and builds a filter", () => {
    expect(setLevel(DEFAULT_VIEW, "brightness", 9).brightness).toBe(2.5);
    expect(cssFilter({ ...DEFAULT_VIEW, brightness: 1.5, contrast: 2 })).toBe("brightness(1.5) contrast(2)");
  });
  it("detects the default view", () => {
    expect(isDefault(DEFAULT_VIEW)).toBe(true);
    expect(isDefault(zoomBy(DEFAULT_VIEW, 0.5))).toBe(false);
  });
});
