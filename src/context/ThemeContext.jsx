/**
 * ThemeContext -- light / dark preference.
 *
 * Tailwind's `darkMode: "class"` strategy means dark styling is driven by a
 * single `dark` class on <html>. This provider owns that class.
 *
 * The FIRST PAINT problem is solved in index.html, not here: an inline script
 * reads localStorage and sets the class before React loads. Without it, a dark
 * mode user sees a white flash on every page load, because React has not
 * mounted yet. That script is small and deliberately duplicated logic.
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";

const ThemeContext = createContext(null);

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => {
    const stored = localStorage.getItem("theme");
    if (stored) return stored;
    // Respect the OS setting when the user has expressed no preference.
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggle, isDark: theme === "dark" }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
