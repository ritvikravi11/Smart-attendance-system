/**
 * LOGIN PAGE
 *
 * LAYOUT DECISION: a split screen, not a centred card on a gradient.
 * The left panel carries the product's thesis -- the register grid itself,
 * rendered as a static motif. Someone who has never seen the app learns what
 * it does before they type anything. A centred card on a gradient would tell
 * them nothing.
 *
 * ERROR HANDLING, deliberately specific:
 *   - The backend returns the same message for a wrong password and an unknown
 *     email. That is an account-enumeration defence, and the UI must not
 *     undermine it by guessing which one it was.
 *   - A locked account (423) DOES get a specific message, because the person
 *     needs to know that waiting will help.
 *   - A network failure names the real cause: the server is not running.
 */

import { ArrowRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { errorCode, errorMessage } from "../api/client";
import { Alert, Button, Input, cx } from "../components/ui/primitives";
import { useAuth } from "../context/AuthContext";

/* Where each role lands after signing in. */
const HOME_BY_ROLE = { student: "/me", faculty: "/faculty", admin: "/faculty" };

/**
 * The register motif: a static grid echoing the live one inside the app.
 * Purely presentational, so it is hidden from assistive tech.
 */
function RegisterMotif() {
  // A fixed pattern rather than random, so the panel looks identical on every
  // load. Random decoration that changes each visit reads as noise.
  const pattern = [
    1, 1, 1, 0, 1, 1, 1, 1, 2, 1, 1, 1,
    1, 0, 1, 1, 1, 1, 3, 1, 1, 1, 1, 0,
    1, 1, 1, 1, 1, 2, 1, 1, 0, 1, 1, 1,
    1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 2, 1,
    1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1,
  ];
  const fills = [
    "bg-white/[0.04] ring-white/[0.06]", // not marked
    "bg-present/25 ring-present/40", // present
    "bg-late/25 ring-late/40", // late
    "bg-absent/25 ring-absent/40", // absent
  ];
  return (
    <div className="grid grid-cols-12 gap-1.5" aria-hidden>
      {pattern.map((state, i) => (
        <div
          key={i}
          className={cx("aspect-[4/3] rounded-[3px] ring-1 ring-inset", fills[state])}
        />
      ))}
    </div>
  );
}

export default function LoginPage() {
  const { login, isAuthenticated, role, loading } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    document.title = "Sign in — Register";
  }, []);

  // Already signed in? Do not show a login form.
  if (!loading && isAuthenticated) {
    return <Navigate to={HOME_BY_ROLE[role] ?? "/me"} replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const me = await login(email.trim(), password);
      navigate(HOME_BY_ROLE[me.role.name] ?? "/me", { replace: true });
    } catch (err) {
      const code = errorCode(err);
      if (code === "ACCOUNT_LOCKED") {
        setError({ title: "Account locked", body: errorMessage(err) });
      } else if (code === "ACCOUNT_INACTIVE") {
        setError({
          title: "Account deactivated",
          body: "Ask your administrator to reactivate it.",
        });
      } else {
        setError({ title: null, body: errorMessage(err) });
      }
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-full">
      {/* LEFT: the thesis panel. Hidden below lg -- on a phone the form is the
          only thing that matters and the motif would push it below the fold. */}
      <div className="relative hidden w-1/2 flex-col justify-between bg-ink-950 p-12 lg:flex">
        <div className="flex items-baseline gap-2">
          <span className="font-display text-lg font-extrabold tracking-tight text-white">
            Register
          </span>
          <span className="font-mono text-[11px] text-ink-500">attendance</span>
        </div>

        <div className="max-w-md">
          <RegisterMotif />
          <h2 className="mt-10 font-display text-[2rem] font-bold leading-[1.1] tracking-tight text-white">
            The register fills itself.
          </h2>
          <p className="mt-3 text-[15px] leading-relaxed text-ink-400">
            Point a camera at the room. Students are recognised and marked as
            they arrive. You keep the final say on every entry.
          </p>
        </div>

        <p className="font-mono text-[11px] text-ink-600">
          Face data stays on the college server. Never shared.
        </p>
      </div>

      {/* RIGHT: the form */}
      <div className="flex w-full items-center justify-center px-5 py-12 lg:w-1/2">
        <div className="w-full max-w-sm">
          <div className="lg:hidden">
            <span className="font-display text-xl font-extrabold tracking-tight text-ink-900 dark:text-white">
              Register
            </span>
          </div>

          <h1 className="mt-8 font-display text-[1.75rem] font-bold tracking-tight text-ink-900 lg:mt-0 dark:text-white">
            Sign in
          </h1>
          <p className="mt-2 text-sm text-ink-500 dark:text-ink-400">
            Use the account your college issued you.
          </p>

          <form onSubmit={handleSubmit} className="mt-8 space-y-4">
            {error && (
              <Alert variant="error" title={error.title}>
                {error.body}
              </Alert>
            )}

            <Input
              label="Email"
              type="email"
              name="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@college.edu"
              autoComplete="username"
              required
              autoFocus
            />

            <Input
              label="Password"
              type="password"
              name="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
              required
            />

            <Button
              type="submit"
              size="lg"
              loading={submitting}
              className="w-full"
              icon={submitting ? undefined : ArrowRight}
            >
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <p className="mt-8 text-[13px] text-ink-400">
            Forgotten your password? Your department admin can reset it.
          </p>
        </div>
      </div>
    </div>
  );
}
