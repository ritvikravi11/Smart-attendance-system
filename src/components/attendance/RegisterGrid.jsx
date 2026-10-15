/**
 * REGISTER GRID  —  the signature component
 * =========================================
 *
 * This is the one element the product is remembered by, and everything else in
 * the UI stays quiet so this can be loud.
 *
 * WHY A GRID AND NOT A LIST
 * -------------------------
 * A paper attendance register is a ruled grid of roll numbers. So is a seating
 * plan. Faculty already read the room that way, so the interface that replaces
 * the register should be readable the same way: at a glance, from a distance,
 * often projected on a classroom screen.
 *
 * A vertical list of 40 names cannot be scanned at a glance -- you have to
 * read it. A grid of 40 tiles can be: you see the shape of the room. Six
 * outlined tiles among thirty-four filled ones is instantly "six missing",
 * with no counting.
 *
 * WHAT EACH TILE ENCODES
 * ----------------------
 *   fill colour  -- status (present / late / absent / excused / not yet marked)
 *   a dot        -- marked by face rather than by hand
 *   a ring       -- needs review, i.e. the match landed in the grey band
 *   the flash    -- this student was recognised in the last moment
 *
 * The flash is the only animation in the app that carries information rather
 * than decorating. It answers the question a faculty member actually has while
 * pointing a camera at a queue: "did that one register?"
 *
 * ACCESSIBILITY
 * -------------
 * Colour alone never carries meaning. Every tile also shows its roll number,
 * has a text title, and the legend states each colour's meaning in words.
 */

import { useEffect, useRef, useState } from "react";

import { cx } from "../ui/primitives";

/* Tile appearance per status. Muted fills with strong text, so forty tiles
   read as a calm field rather than a stained-glass window. */
const TILE_STYLES = {
  present:
    "bg-present-soft text-present-ink ring-present/25 dark:bg-present/15 dark:text-present dark:ring-present/30",
  late: "bg-late-soft text-late-ink ring-late/25 dark:bg-late/15 dark:text-late dark:ring-late/30",
  absent:
    "bg-absent-soft text-absent-ink ring-absent/20 dark:bg-absent/12 dark:text-absent dark:ring-absent/25",
  excused:
    "bg-excused-soft text-excused-ink ring-excused/20 dark:bg-excused/20 dark:text-ink-300 dark:ring-white/10",
  unmarked:
    "bg-white text-ink-400 ring-ink-900/8 dark:bg-ink-800/50 dark:text-ink-500 dark:ring-white/8",
};

const LEGEND = [
  { key: "present", label: "Present" },
  { key: "late", label: "Late" },
  { key: "absent", label: "Absent" },
  { key: "excused", label: "Excused" },
  { key: "unmarked", label: "Not yet marked" },
];

/**
 * One tile.
 *
 * `justMarked` drives the flash. It is derived by the parent from a set of
 * recently-changed student ids and cleared on a timer, so the animation fires
 * exactly once per transition rather than on every re-render.
 */
function Tile({ entry, justMarked, onClick }) {
  const status = entry.status ?? "unmarked";
  const byFace = entry.method === "face";

  return (
    <button
      type="button"
      onClick={() => onClick?.(entry)}
      title={`${entry.roll_number} — ${entry.student_name}${
        entry.confidence ? ` (${(entry.confidence * 100).toFixed(0)}% match)` : ""
      }`}
      className={cx(
        "group relative flex aspect-[4/3] flex-col items-center justify-center",
        "rounded-tile px-1 ring-1 ring-inset transition-all duration-150",
        "hover:z-10 hover:shadow-lift focus-visible:z-10",
        TILE_STYLES[status],
        justMarked && "animate-mark",
        // The review ring is a second, offset ring so it reads as an overlay
        // on the status rather than replacing it -- the student IS present,
        // and additionally needs checking.
        entry.needs_review && "ring-2 ring-late outline outline-1 outline-offset-1 outline-late/40"
      )}
    >
      {/* Roll numbers are the identity unit here, so they get the mono face
          and tabular figures. Truncated to the last 4 characters because a
          roll number like 2226CSE042 is unreadable at tile size -- the tail
          is what distinguishes students within one class. */}
      <span className="font-mono text-[13px] font-semibold leading-none">
        {entry.roll_number.slice(-4)}
      </span>
      <span className="mt-0.5 max-w-full truncate px-0.5 text-[10px] leading-tight opacity-70">
        {entry.student_name.split(" ")[0]}
      </span>

      {/* A filled dot means the AI marked this one. Faculty can see at a
          glance how much of the register the system filled versus how much
          they filled by hand. */}
      {byFace && (
        <span
          className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-current opacity-50"
          aria-hidden
        />
      )}
    </button>
  );
}

export function RegisterGrid({ entries = [], onSelect, recentlyMarked = new Set() }) {
  const counts = entries.reduce((acc, e) => {
    const k = e.status ?? "unmarked";
    acc[k] = (acc[k] || 0) + 1;
    return acc;
  }, {});

  return (
    <div>
      {/*
        Responsive column count. Tight columns on phones, dense on desktop.
        The grid is deliberately allowed to get dense -- 12 columns of 40
        students is exactly the "read the room at a glance" density we want.
      */}
      <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10">
        {entries.map((entry) => (
          <Tile
            key={entry.student_id}
            entry={entry}
            justMarked={recentlyMarked.has(entry.student_id)}
            onClick={onSelect}
          />
        ))}
      </div>

      {/* Legend states each colour in words -- colour is never the only channel. */}
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-ink-900/5 pt-3 dark:border-white/5">
        {LEGEND.map(({ key, label }) => (
          <span key={key} className="flex items-center gap-1.5 text-[11px] text-ink-500 dark:text-ink-400">
            <span
              className={cx("h-2.5 w-2.5 rounded-sm ring-1 ring-inset", TILE_STYLES[key])}
              aria-hidden
            />
            {label}
            {counts[key] ? (
              <span data-numeric className="font-mono font-semibold text-ink-700 dark:text-ink-300">
                {counts[key]}
              </span>
            ) : null}
          </span>
        ))}
        <span className="ml-auto flex items-center gap-1.5 text-[11px] text-ink-500 dark:text-ink-400">
          <span className="h-1.5 w-1.5 rounded-full bg-ink-400" aria-hidden />
          marked by camera
        </span>
      </div>
    </div>
  );
}

/**
 * Tracks which students were marked in the last few seconds, so tiles can
 * flash exactly once.
 *
 * WHY A HOOK RATHER THAN STATE IN THE COMPONENT
 * ---------------------------------------------
 * The roster refreshes roughly twice a second. Comparing "who is present now"
 * against "who was present on the previous render" is the only way to detect a
 * TRANSITION -- and a transition is what deserves an animation. Without this,
 * every tile would re-animate on every poll, which is both noisy and wrong: it
 * would signal "just recognised" for a student marked five minutes ago.
 */
export function useRecentlyMarked(entries, windowMs = 2500) {
  const [recent, setRecent] = useState(() => new Set());
  const previous = useRef(new Map());
  const timers = useRef(new Map());

  useEffect(() => {
    const next = new Map();
    const fresh = [];

    for (const e of entries) {
      next.set(e.student_id, e.status ?? null);
      const before = previous.current.get(e.student_id);
      // A transition from unmarked -> any status is a genuine "just marked"
      // event. previous.current.size guards the first render, where everything
      // would otherwise look new.
      if (previous.current.size > 0 && !before && e.status) {
        fresh.push(e.student_id);
      }
    }
    previous.current = next;

    if (fresh.length === 0) return;

    setRecent((prev) => new Set([...prev, ...fresh]));

    for (const id of fresh) {
      clearTimeout(timers.current.get(id));
      const t = setTimeout(() => {
        setRecent((prev) => {
          const copy = new Set(prev);
          copy.delete(id);
          return copy;
        });
        timers.current.delete(id);
      }, windowMs);
      timers.current.set(id, t);
    }
  }, [entries, windowMs]);

  // Clear pending timers on unmount so we never set state on a dead component.
  useEffect(() => {
    const map = timers.current;
    return () => map.forEach(clearTimeout);
  }, []);

  return recent;
}
