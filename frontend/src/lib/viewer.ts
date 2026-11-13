// Evidence-viewer view state: zoom, rotation, brightness and contrast, kept in bounds.

export type View = { zoom: number; rotate: number; brightness: number; contrast: number };

export const DEFAULT_VIEW: View = { zoom: 1, rotate: 0, brightness: 1, contrast: 1 };

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

export function zoomBy(v: View, step: number): View {
  return { ...v, zoom: Math.round(clamp(v.zoom + step, 0.5, 4) * 100) / 100 };
}

export function rotateBy(v: View, deg: number): View {
  return { ...v, rotate: (((v.rotate + deg) % 360) + 360) % 360 };
}

export function setLevel(v: View, key: "brightness" | "contrast", value: number): View {
  return { ...v, [key]: Math.round(clamp(value, 0.5, 2.5) * 100) / 100 };
}

export function cssFilter(v: View): string {
  return `brightness(${v.brightness}) contrast(${v.contrast})`;
}

export function isDefault(v: View): boolean {
  return v.zoom === 1 && v.rotate === 0 && v.brightness === 1 && v.contrast === 1;
}
