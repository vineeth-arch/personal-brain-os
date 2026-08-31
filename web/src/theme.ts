const THEME_KEY = "cockpit.theme";

export type Theme = "light" | "dark";

// Source of truth is the <html> class, not localStorage — the inline head
// script may have already resolved it before React mounted, and storage
// may be unavailable (private browsing) while the class is still correct.
export function getTheme(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export function applyThemeColorMeta(theme: Theme): void {
  document.querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? "#1F006E" : "#EDEAE2");
}

export function setTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* session-only */ }
  applyThemeColorMeta(theme);
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}
