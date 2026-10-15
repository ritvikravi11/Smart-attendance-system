/**
 * UI PRIMITIVES
 * =============
 * The vocabulary every screen is built from. Grouped in one file because they
 * are small and always used together -- splitting six 20-line components into
 * six files adds navigation cost without adding clarity.
 *
 * The rule they all follow: a primitive knows how it LOOKS, never what the app
 * MEANS. `<Button>` does not know about attendance. `<StatusBadge>` is the one
 * deliberate exception, because attendance status is a closed vocabulary that
 * must render identically everywhere, and centralising it is what guarantees
 * that.
 */

import { AlertCircle, Check, ChevronDown, Loader2 } from "lucide-react";
import { forwardRef } from "react";

/* Tiny class-name joiner. Filters falsy values so conditional classes can be
   written inline as `cond && "class"` without leaving "false" in the output. */
export const cx = (...parts) => parts.filter(Boolean).join(" ");

/* ------------------------------------------------------------------ *
 * BUTTON
 *
 * Variants encode INTENT, not colour. A caller writes variant="danger"
 * rather than className="bg-red-600", so if the danger colour changes it
 * changes in one place -- and no one can accidentally style a save button red.
 * ------------------------------------------------------------------ */

const buttonVariants = {
  primary:
    "bg-accent text-white hover:bg-accent-hover active:bg-accent-hover shadow-sm",
  secondary:
    "bg-white text-ink-800 ring-1 ring-ink-900/10 hover:bg-ink-100/60 dark:bg-ink-800 dark:text-ink-100 dark:ring-white/10 dark:hover:bg-ink-700",
  ghost:
    "text-ink-600 hover:bg-ink-900/5 dark:text-ink-300 dark:hover:bg-white/5",
  danger: "bg-absent text-white hover:bg-absent-ink shadow-sm",
  success: "bg-present text-white hover:bg-present-ink shadow-sm",
};

const buttonSizes = {
  sm: "h-8 px-3 text-[13px] gap-1.5",
  md: "h-10 px-4 text-sm gap-2",
  lg: "h-12 px-6 text-[15px] gap-2",
};

export const Button = forwardRef(function Button(
  {
    // POLYMORPHIC `as`. Added because FacultyDashboard was passing as={Link}
    // to a component that did not support it -- React spread it onto the
    // <button> as an unknown DOM attribute, and the card fell back to
    // `window.location.href`, which triggers a FULL PAGE RELOAD: the bundle
    // re-downloads, React remounts, and the auth context re-fetches the user.
    //
    // Rendering the real element means one component covers buttons and links
    // without duplicating the styling, and links keep their native
    // right-click / middle-click / "open in new tab" behaviour.
    as: Component = "button",
    variant = "primary",
    size = "md",
    loading = false,
    disabled = false,
    icon: Icon,
    className,
    children,
    ...props
  },
  ref
) {
  const isNativeButton = Component === "button";
  return (
    <Component
      ref={ref}
      // `disabled` and `type` are only valid on a real <button>. Setting them
      // on an <a> produces invalid HTML and a React warning.
      {...(isNativeButton
        ? {
            // Disabled while loading, so a double-click cannot fire two
            // requests -- the cheapest defence against duplicate submission.
            disabled: disabled || loading,
          }
        : {
            // A link cannot be natively disabled, so express the state to
            // assistive tech and block the interaction.
            "aria-disabled": disabled || loading || undefined,
            tabIndex: disabled || loading ? -1 : undefined,
          })}
      className={cx(
        "inline-flex select-none items-center justify-center rounded-lg font-medium",
        "transition-colors duration-150",
        "disabled:cursor-not-allowed disabled:opacity-50",
        buttonVariants[variant],
        buttonSizes[size],
        className
      )}
      {...props}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      ) : Icon ? (
        <Icon className="h-4 w-4" aria-hidden />
      ) : null}
      {children}
    </Component>
  );
});

/* ------------------------------------------------------------------ *
 * INPUT
 *
 * Label, field, hint, and error are one component because they must stay
 * connected. `htmlFor`/`id` linking makes the label clickable and read aloud
 * by screen readers; `aria-invalid` and `aria-describedby` connect the error
 * text to the field. Splitting these apart is how that wiring gets forgotten.
 * ------------------------------------------------------------------ */

export const Input = forwardRef(function Input(
  { label, error, hint, id, className, ...props },
  ref
) {
  const fieldId = id || `f-${props.name || Math.random().toString(36).slice(2)}`;
  const errorId = `${fieldId}-error`;

  return (
    <div className="space-y-1.5">
      {label && (
        <label
          htmlFor={fieldId}
          className="block text-[13px] font-medium text-ink-700 dark:text-ink-300"
        >
          {label}
        </label>
      )}
      <input
        ref={ref}
        id={fieldId}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? errorId : undefined}
        className={cx(
          "block w-full rounded-lg border-0 px-3.5 py-2.5 text-sm",
          "bg-white text-ink-900 ring-1 ring-inset ring-ink-900/10",
          "placeholder:text-ink-400",
          "focus:ring-2 focus:ring-inset focus:ring-accent",
          "dark:bg-ink-800 dark:text-ink-100 dark:ring-white/10",
          error && "ring-absent focus:ring-absent",
          className
        )}
        {...props}
      />
      {error && (
        <p id={errorId} className="flex items-start gap-1.5 text-[13px] text-absent">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {error}
        </p>
      )}
      {hint && !error && (
        <p className="text-[13px] text-ink-400 dark:text-ink-500">{hint}</p>
      )}
    </div>
  );
});

/* ------------------------------------------------------------------ *
 * CARD
 *
 * The single surface primitive. Every panel in the app is a Card, so surfaces
 * cannot drift apart into five slightly different shadows and radii.
 * ------------------------------------------------------------------ */

/*
 * forwardRef, because MarkSheet needs a ref to the panel for its focus trap.
 * A function component silently DROPS a ref prop -- React logs a warning in
 * development and nothing at all in production, so the focus trap would have
 * appeared to work while doing nothing. Any primitive that might be measured,
 * focused, or scrolled needs to forward its ref.
 */
export const Card = forwardRef(function Card({ className, children, ...props }, ref) {
  return (
    <div ref={ref} className={cx("card", className)} {...props}>
      {children}
    </div>
  );
});

export function CardHeader({ eyebrow, title, action, className }) {
  return (
    <div
      className={cx(
        "flex items-start justify-between gap-4 border-b border-ink-900/5 px-5 py-4 dark:border-white/5",
        className
      )}
    >
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-1">{eyebrow}</p>}
        <h2 className="truncate font-display text-title text-ink-900 dark:text-ink-100">
          {title}
        </h2>
      </div>
      {action}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * STATUS BADGE  —  the app's shared vocabulary
 *
 * The one primitive that knows domain meaning, deliberately. Attendance status
 * appears in the roster, the reports table, the student dashboard and the live
 * camera overlay. If each rendered its own colours they would drift, and a
 * student would learn that green means present in one place and something else
 * in another. Defining it once makes the vocabulary consistent.
 *
 * THE IMPORTANT DISTINCTION: `unknown` is violet, not red. An unrecognised
 * face is a STRANGER IN THE ROOM. An absent student is someone who did not
 * come. Those are different events needing different responses, so they must
 * not share a colour.
 * ------------------------------------------------------------------ */

const STATUS_STYLES = {
  present: "bg-present-soft text-present-ink dark:bg-present/15 dark:text-present",
  late: "bg-late-soft text-late-ink dark:bg-late/15 dark:text-late",
  absent: "bg-absent-soft text-absent-ink dark:bg-absent/15 dark:text-absent",
  excused: "bg-excused-soft text-excused-ink dark:bg-excused/20 dark:text-ink-300",
  unknown: "bg-unknown-soft text-unknown-ink dark:bg-unknown/15 dark:text-unknown",
  unmarked:
    "bg-ink-100 text-ink-500 dark:bg-ink-800 dark:text-ink-400 ring-1 ring-inset ring-ink-900/5 dark:ring-white/5",
};

const STATUS_LABELS = {
  present: "Present",
  late: "Late",
  absent: "Absent",
  excused: "Excused",
  unknown: "Unknown",
  unmarked: "Not marked",
};

export function StatusBadge({ status, size = "md", className }) {
  const key = status || "unmarked";
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-full font-medium",
        size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs",
        STATUS_STYLES[key] ?? STATUS_STYLES.unmarked,
        className
      )}
    >
      {STATUS_LABELS[key] ?? key}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * SPINNER + SKELETON
 *
 * Two different loading affordances for two different situations:
 *   Spinner  -- an action is running ("saving...")
 *   Skeleton -- content is arriving, and we know its SHAPE
 *
 * Skeletons are better for page loads because they preserve layout: the page
 * does not jump when data lands. A centred spinner followed by a full page is
 * a visible reflow every time.
 * ------------------------------------------------------------------ */

export function Spinner({ className }) {
  return (
    <Loader2
      className={cx("h-5 w-5 animate-spin text-ink-400", className)}
      aria-label="Loading"
    />
  );
}

export function Skeleton({ className }) {
  return (
    <div
      className={cx(
        "animate-pulse rounded-md bg-ink-900/[0.06] dark:bg-white/[0.06]",
        className
      )}
      aria-hidden
    />
  );
}

/* ------------------------------------------------------------------ *
 * EMPTY STATE
 *
 * "An empty screen is an invitation to act." Every empty state names what is
 * missing AND offers the action that fills it. A bare "No data" tells a person
 * nothing they did not already know.
 * ------------------------------------------------------------------ */

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {Icon && (
        <div className="mb-4 rounded-full bg-ink-900/5 p-3 dark:bg-white/5">
          <Icon className="h-6 w-6 text-ink-400" aria-hidden />
        </div>
      )}
      <h3 className="font-display text-base font-semibold text-ink-800 dark:text-ink-200">
        {title}
      </h3>
      {description && (
        <p className="mt-1.5 max-w-sm text-sm text-ink-500 dark:text-ink-400">
          {description}
        </p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * SELECT
 * ------------------------------------------------------------------ */

export function Select({ label, options = [], className, ...props }) {
  return (
    <div className="space-y-1.5">
      {label && (
        <label className="block text-[13px] font-medium text-ink-700 dark:text-ink-300">
          {label}
        </label>
      )}
      <div className="relative">
        <select
          className={cx(
            "block w-full appearance-none rounded-lg border-0 py-2.5 pl-3.5 pr-9 text-sm",
            "bg-white text-ink-900 ring-1 ring-inset ring-ink-900/10",
            "focus:ring-2 focus:ring-inset focus:ring-accent",
            "dark:bg-ink-800 dark:text-ink-100 dark:ring-white/10",
            className
          )}
          {...props}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400"
          aria-hidden
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * ALERT
 *
 * Errors "explain what went wrong and how to fix it" -- so this takes a
 * description, not just a title. A red box saying "Error" helps nobody.
 * ------------------------------------------------------------------ */

const ALERT_STYLES = {
  error: "bg-absent-soft text-absent-ink dark:bg-absent/10 dark:text-absent",
  success: "bg-present-soft text-present-ink dark:bg-present/10 dark:text-present",
  warning: "bg-late-soft text-late-ink dark:bg-late/10 dark:text-late",
  info: "bg-accent-soft text-accent dark:bg-accent/10 dark:text-accent",
};

export function Alert({ variant = "info", title, children, className }) {
  const Icon = variant === "success" ? Check : AlertCircle;
  return (
    <div
      role={variant === "error" ? "alert" : "status"}
      className={cx("flex gap-3 rounded-lg px-4 py-3 text-sm", ALERT_STYLES[variant], className)}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={cx(title && "mt-0.5", "opacity-90")}>{children}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * STAT
 *
 * A labelled number. Uses the display face and tabular figures so a row of
 * these aligns and does not jitter as values update.
 * ------------------------------------------------------------------ */

export function Stat({ label, value, sub, tone = "default", className }) {
  const tones = {
    default: "text-ink-900 dark:text-ink-100",
    present: "text-present",
    absent: "text-absent",
    late: "text-late",
    muted: "text-ink-400",
  };
  return (
    <div className={className}>
      <p className="eyebrow">{label}</p>
      <p
        data-numeric
        className={cx("mt-1.5 font-display text-[1.75rem] leading-none", tones[tone])}
      >
        {value}
      </p>
      {sub && <p className="mt-1.5 text-[13px] text-ink-500 dark:text-ink-400">{sub}</p>}
    </div>
  );
}
