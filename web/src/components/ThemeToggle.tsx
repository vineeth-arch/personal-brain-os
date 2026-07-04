import { useEffect, useState } from "react";
import { applyThemeColorMeta, getTheme, toggleTheme } from "../theme";

export function ThemeToggle() {
  const [theme, setThemeState] = useState(getTheme);

  // one-time correction: if the head script already resolved to light
  // before mount, the static <meta theme-color> still says dark — fix it.
  useEffect(() => applyThemeColorMeta(theme), []);

  return (
    <button
      type="button"
      onClick={() => setThemeState(toggleTheme())}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      aria-pressed={theme === "dark"}
      className="border-subtle text-subtle hover:border-emphasis flex h-11 w-11 items-center justify-center rounded-full border"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        {theme === "dark" ? (
          <> {/* sun — invites switching to light */}
            <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.8" />
            <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"
              stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </>
        ) : ( /* moon — invites switching to dark */
          <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"
            stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
        )}
      </svg>
    </button>
  );
}
