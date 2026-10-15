/**
 * CAMERA STAGE  —  live video with recognition overlay.
 *
 * THE COORDINATE PROBLEM, which is the whole difficulty of this component
 * ----------------------------------------------------------------------
 * The backend returns bounding boxes in the coordinate space of the frame it
 * received -- say 1280x720. The <video> element on screen might be 640x360, or
 * 900x506 on a wide monitor. Drawing the raw coordinates puts every box in the
 * wrong place, and the error grows with the mismatch.
 *
 * The fix is to render the overlay in PERCENTAGES of the video's intrinsic
 * dimensions. A box at x=640 of a 1280-wide frame is at 50%. Percentages
 * survive any display size, so the overlay stays correct when the window is
 * resized, on a projector, and on a phone -- with no resize listener and no
 * recalculation.
 *
 * WHY AN ABSOLUTELY POSITIONED DIV RATHER THAN <canvas>
 * -----------------------------------------------------
 * A canvas would need manual redraw on every resize and every frame, plus
 * hand-drawn text. DOM elements give us real text rendering, real fonts, CSS
 * transitions between positions, and screen-reader access to the labels, for
 * less code.
 */

import { Camera, CameraOff, Loader2 } from "lucide-react";

import { Alert, Button, cx } from "../ui/primitives";

/* Overlay colour per outcome. Matches the StatusBadge vocabulary exactly, so a
   face outlined violet on camera and a violet badge in the log mean the same
   thing. */
const BOX_STYLES = {
  marked: { box: "border-present", label: "bg-present text-white" },
  already: { box: "border-present/60", label: "bg-present/75 text-white" },
  review: { box: "border-late", label: "bg-late text-white" },
  unknown: { box: "border-unknown", label: "bg-unknown text-white" },
  rejected: { box: "border-ink-400 border-dashed", label: "bg-ink-700 text-white" },
};

function classify(face) {
  if (!face.quality_passed) return "rejected";
  if (face.is_unknown) return "unknown";
  if (face.needs_review) return "review";
  if (face.marked) return "marked";
  return "already";
}

function labelFor(face, kind) {
  if (kind === "rejected") return face.quality_reason;
  if (kind === "unknown") return `Unknown · ${(face.confidence * 100).toFixed(0)}%`;
  const name = face.student_name?.split(" ")[0] ?? "?";
  const pct = `${(face.confidence * 100).toFixed(0)}%`;
  if (kind === "review") return `${name} · ${pct} · check`;
  return `${name} · ${pct}`;
}

export function CameraStage({
  videoRef,
  active,
  starting,
  error,
  faces = [],
  frameSize,
  onStart,
  onStop,
  scanning = false,
}) {
  return (
    <div className="space-y-3">
      <div className="relative overflow-hidden rounded-card bg-ink-950 ring-1 ring-ink-900/10 dark:ring-white/10">
        {/* aspect-video reserves the correct space before the stream arrives,
            so the page does not jump when video starts. */}
        <div className="relative aspect-video w-full">
          <video
            ref={videoRef}
            playsInline
            muted
            // scale-x-[-1] mirrors the preview. People expect a mirror when
            // looking at themselves -- an unmirrored self-view feels wrong and
            // makes them move the wrong way to centre their face. The frame
            // SENT to the server is unmirrored, so recognition is unaffected.
            className={cx(
              "h-full w-full scale-x-[-1] object-cover transition-opacity duration-300",
              active ? "opacity-100" : "opacity-0"
            )}
          />

          {/* OVERLAY. Also mirrored, so boxes land on the mirrored faces. */}
          {active && frameSize?.width > 0 && (
            <div className="pointer-events-none absolute inset-0 scale-x-[-1]">
              {faces.map((face, i) => {
                const [x1, y1, x2, y2] = face.bbox;
                const kind = classify(face);
                const style = BOX_STYLES[kind];

                // Percentages, not pixels. This is the whole trick.
                const left = (x1 / frameSize.width) * 100;
                const top = (y1 / frameSize.height) * 100;
                const w = ((x2 - x1) / frameSize.width) * 100;
                const h = ((y2 - y1) / frameSize.height) * 100;

                return (
                  <div
                    key={`${i}-${face.student_id ?? "u"}`}
                    className={cx(
                      "absolute rounded-md border-2 transition-all duration-150",
                      style.box
                    )}
                    style={{ left: `${left}%`, top: `${top}%`, width: `${w}%`, height: `${h}%` }}
                  >
                    {/* Label is un-mirrored back so text reads correctly. */}
                    <span
                      className={cx(
                        "absolute -top-6 left-0 scale-x-[-1] whitespace-nowrap rounded px-1.5 py-0.5",
                        "font-mono text-[11px] font-medium",
                        style.label
                      )}
                    >
                      {labelFor(face, kind)}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Idle state. An empty screen is an invitation to act, so it says
              what will happen rather than just sitting dark. */}
          {!active && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-center">
              {starting ? (
                <>
                  <Loader2 className="h-7 w-7 animate-spin text-ink-400" />
                  <p className="text-sm text-ink-400">Starting camera…</p>
                </>
              ) : (
                <>
                  <div className="rounded-full bg-white/5 p-4">
                    <CameraOff className="h-7 w-7 text-ink-400" aria-hidden />
                  </div>
                  <div className="max-w-xs">
                    <p className="font-display text-base font-semibold text-ink-100">
                      Camera is off
                    </p>
                    <p className="mt-1 text-[13px] text-ink-400">
                      Start it to mark students as they arrive. You can also mark
                      anyone by hand from the register.
                    </p>
                  </div>
                  <Button variant="primary" icon={Camera} onClick={onStart}>
                    Start camera
                  </Button>
                </>
              )}
            </div>
          )}

          {/* Scanning indicator: a slow sweep, only while a request is in
              flight. It answers "is this thing actually working?" without a
              spinner covering the faces. */}
          {active && scanning && (
            <div className="pointer-events-none absolute inset-x-0 top-0 h-full overflow-hidden">
              <div className="h-px w-full animate-scan bg-gradient-to-r from-transparent via-accent to-transparent" />
            </div>
          )}

          {/* Live badge */}
          {active && (
            <div className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-ink-950/70 px-2.5 py-1 backdrop-blur">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-present" aria-hidden />
              <span className="text-[11px] font-medium uppercase tracking-wider text-ink-100">
                Live
              </span>
            </div>
          )}
        </div>
      </div>

      {error && (
        <Alert variant="error" title="Camera unavailable">
          {error}
        </Alert>
      )}

      {active && (
        <div className="flex justify-end">
          <Button variant="secondary" size="sm" icon={CameraOff} onClick={onStop}>
            Stop camera
          </Button>
        </div>
      )}
    </div>
  );
}
