/**
 * APP SHELL  —  persistent chrome around every authenticated page.
 *
 * WHY A SHELL COMPONENT
 * ---------------------
 * Navigation, theme toggle, and user menu appear on every page. Putting them
 * in a shell that renders <Outlet /> means they mount ONCE and survive route
 * changes -- so the sidebar does not flicker or lose scroll position when you
 * navigate. It also means a new page is one route entry, not a copy-pasted
 * layout.
 *
 * NAVIGATION IS ROLE-DERIVED
 * --------------------------
 * The link list is built from the user's role, so a student never sees a
 * "Take attendance" link that would 403.
 *
 * To be explicit, because it matters: THIS IS NOT SECURITY. Hiding a link
 * stops nobody -- anyone can type the URL or call the API directly. The real
 * enforcement is the `require_role` dependency on the server. This is a
 * usability feature: don't show people doors they cannot open.
 */

import {
  BookOpen,
  ChartNoAxesColumn,
  ChartPie,
  ClipboardList,
  LogOut,
  Menu,
  Moon,
  ScanFace,
  Sun,
  X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { ErrorBoundary } from "../ErrorBoundary";
import { useAuth } from "../../context/AuthContext";
import { useTheme } from "../../context/ThemeContext";
import { cx } from "../ui/primitives";

const NAV_BY_ROLE = {
  student: [
    { to: "/me", label: "My attendance", icon: BookOpen },
  ],
  faculty: [
    { to: "/faculty", label: "My classes", icon: ClipboardList },
    { to: "/attendance", label: "Take attendance", icon: ScanFace },
    { to: "/analytics", label: "Analytics", icon: ChartNoAxesColumn },
    { to: "/reports", label: "Reports", icon: ClipboardList },
  ],
  admin: [
    { to: "/faculty", label: "Classes", icon: ClipboardList },
    { to: "/attendance", label: "Take attendance", icon: ScanFace },
    { to: "/analytics", label: "Analytics", icon: ChartNoAxesColumn },
    { to: "/reports", label: "Reports", icon: ClipboardList },
  ],
};

/** The wordmark. Set in the display face at a tight tracking so it reads as a
 *  mark rather than a heading. */
function Wordmark() {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="font-display text-[15px] font-extrabold tracking-tight text-white">
        Register
      </span>
      <span className="font-mono text-[10px] text-ink-400">v1</span>
    </div>
  );
}

function NavItem({ to, label, icon: Icon, onNavigate }) {
  return (
    <NavLink
      to={to}
      onClick={onNavigate}
      className={({ isActive }) =>
        cx(
          "flex items-center gap-3 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors",
          isActive
            ? "bg-white/10 text-white"
            : "text-ink-300 hover:bg-white/5 hover:text-white"
        )
      }
    >
      {({ isActive }) => (
        <>
          {/* A left rule marks the active item. It encodes position in the nav,
              which is real information, rather than decorating the link. */}
          <span
            className={cx(
              "h-4 w-0.5 rounded-full transition-colors",
              isActive ? "bg-accent" : "bg-transparent"
            )}
            aria-hidden
          />
          <Icon className="h-4 w-4 shrink-0" aria-hidden />
          {label}
        </>
      )}
    </NavLink>
  );
}

export function AppShell() {
  const { user, role, logout } = useAuth();
  const { isDark, toggle } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);

  const links = NAV_BY_ROLE[role] ?? [];

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  const sidebar = (
    <div className="flex h-full flex-col bg-ink-900 dark:bg-ink-950">
      <div className="flex h-14 items-center justify-between px-4">
        <Wordmark />
        <button
          type="button"
          onClick={() => setMobileOpen(false)}
          className="rounded-lg p-1.5 text-ink-400 hover:bg-white/5 lg:hidden"
          aria-label="Close navigation"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <nav className="flex-1 space-y-0.5 px-2.5 py-2">
        {links.map((l) => (
          <NavItem key={l.to} {...l} onNavigate={() => setMobileOpen(false)} />
        ))}
      </nav>

      {/* User block pinned to the bottom -- a conventional position, so people
          find it without looking. */}
      <div className="border-t border-white/5 p-2.5">
        <div className="flex items-center gap-3 rounded-lg px-3 py-2">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/20 font-display text-[13px] font-semibold text-accent">
            {user?.full_name?.[0]?.toUpperCase() ?? "?"}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium text-white">
              {user?.full_name}
            </p>
            <p className="truncate text-[11px] capitalize text-ink-400">{role}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          className="mt-0.5 flex w-full items-center gap-3 rounded-lg px-3 py-2 text-[13px] font-medium text-ink-300 transition-colors hover:bg-white/5 hover:text-white"
        >
          <span className="h-4 w-0.5" aria-hidden />
          <LogOut className="h-4 w-4" aria-hidden />
          Log out
        </button>
      </div>
    </div>
  );

  return (
    <div className="flex h-full">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 lg:block">{sidebar}</aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-ink-950/60 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
          <div className="absolute inset-y-0 left-0 w-64 animate-fade-up">{sidebar}</div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-ink-900/5 bg-white/80 px-4 backdrop-blur dark:border-white/5 dark:bg-ink-900/80">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="rounded-lg p-1.5 text-ink-500 hover:bg-ink-900/5 lg:hidden dark:hover:bg-white/5"
            aria-label="Open navigation"
          >
            <Menu className="h-5 w-5" />
          </button>

          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              onClick={toggle}
              className="rounded-lg p-2 text-ink-500 transition-colors hover:bg-ink-900/5 dark:text-ink-400 dark:hover:bg-white/5"
              aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
            >
              {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto">
          {/*
            Inner boundary, INSIDE the shell. If a page crashes, the sidebar
            and topbar survive -- so the user can navigate somewhere that
            works instead of being stranded. A single top-level boundary would
            take the navigation down with the page.

            The `key` resets the boundary on every route change: without it, a
            crash on one page would leave the error state showing after the
            user navigates away.
          */}
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

/** Consistent page header. Keeps the eyebrow/title rhythm identical everywhere. */
export function PageHeader({ eyebrow, title, description, action }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 pb-6">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
        <h1 className="font-display text-display text-ink-900 dark:text-ink-50">
          {title}
        </h1>
        {description && (
          <p className="mt-2 max-w-2xl text-sm text-ink-500 dark:text-ink-400">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

/** Page container. One place controls max width and gutters. */
export function Page({ children, className }) {
  return (
    <div className={cx("mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8", className)}>
      {children}
    </div>
  );
}
