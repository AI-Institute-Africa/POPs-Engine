// Theme: "light" | "dark" | "auto" (follows the OS). Stored in localStorage.
import { useEffect, useState } from "react";

export type ThemeChoice = "light" | "dark" | "auto";

const KEY = "validate-theme";
const media = window.matchMedia("(prefers-color-scheme: dark)");

function stored(): ThemeChoice {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : "auto";
}

function apply(choice: ThemeChoice) {
  const resolved = choice === "auto" ? (media.matches ? "dark" : "light") : choice;
  document.documentElement.dataset.theme = resolved;
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(() => stored());

  useEffect(() => {
    apply(choice);
    if (choice === "auto") {
      media.addEventListener("change", () => apply("auto"));
      return () => media.removeEventListener("change", () => apply("auto"));
    }
  }, [choice]);

  const cycle = () => {
    const next: ThemeChoice = choice === "auto" ? "light" : choice === "light" ? "dark" : "auto";
    localStorage.setItem(KEY, next);
    setChoice(next);
  };

  const icon = choice === "auto" ? "🖥" : choice === "dark" ? "🌙" : "☀";
  const label = choice === "auto" ? "Theme: auto" : choice === "dark" ? "Theme: dark" : "Theme: light";
  return { choice, cycle, icon, label };
}
