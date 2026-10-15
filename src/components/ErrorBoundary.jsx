/**
 * ERROR BOUNDARY
 *
 * WHY THIS WAS MISSING AND WHY IT MATTERS
 * ---------------------------------------
 * React has one unforgiving default: an uncaught error during render unmounts
 * the ENTIRE component tree. Not the broken card -- everything. The user gets a
 * blank white page with no message, no way back, and nothing in the UI to
 * suggest what happened.
 *
 * The failure is not hypothetical for this app. `dashboard.summary.class_average`
 * is null when no classes have been held; one `.toFixed()` on a null anywhere in
 * the tree blanks the whole application. That is a one-character bug producing a
 * total outage.
 *
 * An error boundary catches it, shows something useful, and keeps the rest of the
 * app alive.
 *
 * WHY IT MUST BE A CLASS COMPONENT
 * --------------------------------
 * There is no hook equivalent. `componentDidCatch` and
 * `getDerivedStateFromError` have no useEffect counterpart -- React has said
 * repeatedly that a hooks version is not planned. This is the one place a class
 * component is still mandatory in modern React, which is exactly why teams
 * forget to add one.
 *
 * WHAT IT DOES NOT CATCH
 * ----------------------
 * Deliberately stating the limits, because assuming otherwise is worse than
 * having no boundary at all:
 *   - errors inside event handlers (use try/catch there)
 *   - errors in async code (a rejected promise is not a render error)
 *   - errors during server-side rendering
 *   - errors thrown by the boundary itself
 *
 * Our API calls are all in try/catch already, so the gap is covered.
 */

import { RefreshCw, TriangleAlert } from "lucide-react";
import { Component } from "react";

import { Button, Card } from "./ui/primitives";

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    // Runs during the render phase, so it must be pure -- no side effects,
    // no logging. It exists only to flip state so the fallback renders.
    return { error };
  }

  componentDidCatch(error, errorInfo) {
    // Runs during the commit phase, where side effects are allowed.
    // The component stack is the useful part -- it tells you WHICH component
    // threw, which the error message alone does not.
    console.error("Render error caught by boundary:", error, errorInfo);
    this.setState({ errorInfo });

    // Where an error reporting service would go. Deliberately not wired to
    // one: shipping user data to a third party is a decision the institution
    // should make, not a default.
  }

  handleReset = () => {
    // Clearing the error re-renders the children. If the underlying data is
    // still bad it will throw again immediately -- which is correct. It gives
    // a transient failure a way back without hiding a persistent one.
    this.setState({ error: null, errorInfo: null });
  };

  render() {
    if (!this.state.error) return this.props.children;

    const isDev = import.meta.env.DEV;

    return (
      <div className="flex min-h-[60vh] items-center justify-center p-6">
        <Card className="w-full max-w-lg p-6">
          <div className="flex items-start gap-4">
            <div className="rounded-full bg-absent-soft p-2.5 dark:bg-absent/15">
              <TriangleAlert className="h-5 w-5 text-absent" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="font-display text-title text-ink-900 dark:text-ink-100">
                Something went wrong on this page
              </h1>
              <p className="mt-2 text-sm text-ink-500 dark:text-ink-400">
                Your attendance data is safe — this is a display problem, not a
                data problem. Try reloading; if it keeps happening, tell your
                administrator what you were doing.
              </p>

              {/*
                The technical detail appears in development only. In production
                a stack trace discloses file paths and library versions to
                anyone who can trigger the error -- the same reasoning as the
                backend's generic 500 handler.
              */}
              {isDev && (
                <details className="mt-4">
                  <summary className="cursor-pointer text-[13px] font-medium text-ink-600 dark:text-ink-300">
                    Technical detail (development only)
                  </summary>
                  <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-ink-950 p-3 font-mono text-[11px] leading-relaxed text-ink-200">
                    {this.state.error.toString()}
                    {this.state.errorInfo?.componentStack}
                  </pre>
                </details>
              )}

              <div className="mt-5 flex gap-2">
                <Button icon={RefreshCw} onClick={this.handleReset}>
                  Try again
                </Button>
                <Button variant="secondary" onClick={() => window.location.reload()}>
                  Reload the page
                </Button>
              </div>
            </div>
          </div>
        </Card>
      </div>
    );
  }
}
