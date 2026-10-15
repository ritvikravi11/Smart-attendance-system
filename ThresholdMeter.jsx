/**
 * THRESHOLD METER  —  the student-facing signature element.
 *
 * WHY THIS EXISTS INSTEAD OF A PERCENTAGE LABEL
 * ---------------------------------------------
 * For a student, attendance is not a statistic. In most Indian engineering
 * colleges 75% is the line that decides whether you can sit the exam. The only
 * questions a student actually has are:
 *
 *     Am I above the line?
 *     If not, how many classes until I am?
 *
 * A number alone answers neither well -- "68%" requires you to remember the
 * threshold and do arithmetic. So the threshold IS the design: a marked line
 * on every bar, with the fill reaching past it or falling short.
 *
 * The bar is not decoration. It encodes the one comparison that matters, and
 * the dashed line makes that comparison instant across a list of six subjects.
 *
 * The recovery count comes from the backend's `shortfall_sessions`, solved
 * from (attended + n) / (denominator + n) >= target. Telling a student "attend
 * 12 consecutive classes" is actionable in a way "68%" is not.
 *
 * NULL IS NOT ZERO
 * ----------------
 * When no sessions have happened, `percentage` is null. Rendering that as 0%
 * would tell every student on day one that they are failing. The component
 * shows an em-dash and says so plainly.
 */

import { cx } from "../ui/primitives";

const THRESHOLD = 75;

export function ThresholdMeter({
  percentage,
  threshold = THRESHOLD,
  attended,
  denominator,
  shortfall,
  size = "md",
  showScale = true,
}) {
  // No data is a distinct state, not a zero.
  if (percentage === null || percentage === undefined) {
    return (
      <div className="space-y-1.5">
        <div className="flex items-baseline gap-2">
          <span className="font-display text-2xl text-ink-300 dark:text-ink-600">—</span>
          <span className="text-[13px] text-ink-400">No classes held yet</span>
        </div>
        <div className="h-2 w-full rounded-full bg-ink-900/5 dark:bg-white/5" />
      </div>
    );
  }

  const safe = percentage >= threshold;
  const clamped = Math.max(0, Math.min(100, percentage));

  const heights = { sm: "h-1.5", md: "h-2.5", lg: "h-3.5" };
  const numberSizes = { sm: "text-lg", md: "text-[1.75rem]", lg: "text-display" };

  return (
    <div className="space-y-2">
      <div className="flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span
            data-numeric
            className={cx(
              "font-display leading-none",
              numberSizes[size],
              safe ? "text-present" : "text-absent"
            )}
          >
            {percentage.toFixed(1)}%
          </span>
          {attended !== undefined && (
            <span data-numeric className="font-mono text-[13px] text-ink-400">
              {attended}/{denominator}
            </span>
          )}
        </div>

        {/* The verdict, in words. Colour is never the only channel. */}
        <span
          className={cx(
            "text-[13px] font-medium",
            safe ? "text-present" : "text-absent"
          )}
        >
          {safe ? "Above the line" : `${(threshold - percentage).toFixed(1)}% short`}
        </span>
      </div>

      {/* THE BAR. The dashed marker at the threshold is the whole idea. */}
      <div className="relative">
        <div
          className={cx(
            "w-full overflow-hidden rounded-full bg-ink-900/[0.07] dark:bg-white/[0.07]",
            heights[size]
          )}
        >
          <div
            className={cx(
              "h-full rounded-full transition-[width] duration-500 ease-out",
              safe ? "bg-present" : "bg-absent"
            )}
            style={{ width: `${clamped}%` }}
            role="progressbar"
            aria-valuenow={Math.round(percentage)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Attendance ${percentage.toFixed(1)} percent, threshold ${threshold} percent`}
          />
        </div>

        {/* Threshold marker, drawn over the bar */}
        <div
          className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-ink-900/40 dark:bg-white/50"
          style={{ left: `${threshold}%` }}
          aria-hidden
        />
        {showScale && (
          <span
            className="absolute top-full mt-1 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] text-ink-400"
            style={{ left: `${threshold}%` }}
            aria-hidden
          >
            {threshold}% required
          </span>
        )}
      </div>

      {/* The actionable part: what to actually do about it. */}
      {!safe && shortfall > 0 && (
        <p className={cx("text-[13px] text-ink-500 dark:text-ink-400", showScale && "pt-3")}>
          Attend the next{" "}
          <span data-numeric className="font-mono font-semibold text-ink-800 dark:text-ink-200">
            {shortfall}
          </span>{" "}
          {shortfall === 1 ? "class" : "classes"} in a row to reach {threshold}%.
        </p>
      )}
    </div>
  );
}
