/**
 * ATTENDANCE SCREEN  —  the demo centrepiece.
 *
 * LAYOUT: camera left, register right. This is the whole design decision.
 *
 * A lecturer standing at the front needs both at once: the camera to aim, and
 * the register to see who is still missing. Putting the register behind a tab
 * would mean toggling back and forth during the one moment the room is filling
 * up. Side by side, the page answers "who is still missing?" continuously.
 *
 * On a phone they stack -- camera first, because you cannot aim what you
 * cannot see.
 *
 * THE CAPTURE LOOP
 * ----------------
 * setInterval would be wrong. If the server takes 800ms and the interval is
 * 500ms, requests pile up faster than they complete and the queue grows
 * without bound until the tab dies.
 *
 * Instead the loop is self-scheduling: capture, await the response, wait the
 * remaining time, repeat. The rate adapts to whatever the server can actually
 * sustain. `inFlight` guards against overlap; `runningRef` lets us stop
 * cleanly on unmount.
 */

import { Play, Square, Users } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import { sessionApi } from "../api/endpoints";
import { CameraStage } from "../components/attendance/CameraStage";
import {
  RegisterGrid,
  useRecentlyMarked,
} from "../components/attendance/RegisterGrid";
import { Page, PageHeader } from "../components/layout/AppShell";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  Input,
  StatusBadge,
  cx,
} from "../components/ui/primitives";
import { useCamera } from "../hooks/useCamera";

const CAPTURE_INTERVAL_MS = 500; // ~2 fps, matching the backend's budget

/**
 * Manual marking sheet, opened by tapping a register tile.
 *
 * ACCESSIBILITY FIXES. This was a plain div with a click-to-dismiss backdrop:
 *   - no role, so screen readers announced it as ordinary page content
 *   - no Escape key, so keyboard users could not close it
 *   - no focus management, so focus stayed behind the overlay and Tab walked
 *     through the page underneath -- the classic broken-modal experience
 *
 * A modal that traps sighted mouse users but not keyboard users is not "mostly
 * accessible"; it is a dead end for anyone who cannot click.
 */
function MarkSheet({ entry, onClose, onMark, busy }) {
  const panelRef = useRef(null);
  const previouslyFocused = useRef(null);

  useEffect(() => {
    if (!entry) return;

    // Remember where focus came from so it can be restored on close --
    // otherwise focus jumps to the top of the document and the user loses
    // their place in a 40-tile grid.
    previouslyFocused.current = document.activeElement;
    panelRef.current?.focus();

    function onKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      // FOCUS TRAP: cycle within the dialog rather than escaping behind it.
      const focusable = panelRef.current?.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused.current?.focus?.();
    };
  }, [entry, onClose]);

  if (!entry) return null;
  const options = [
    { status: "present", label: "Present", variant: "success" },
    { status: "late", label: "Late", variant: "secondary" },
    { status: "absent", label: "Absent", variant: "danger" },
    { status: "excused", label: "Excused", variant: "secondary" },
  ];
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center">
      <div
        className="absolute inset-0 bg-ink-950/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <Card
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="mark-sheet-title"
        tabIndex={-1}
        className="relative w-full max-w-sm animate-fade-up p-5 focus:outline-none sm:rounded-card"
      >
        <p className="font-mono text-[13px] font-semibold text-ink-500">
          {entry.roll_number}
        </p>
        <h3
          id="mark-sheet-title"
          className="mt-1 font-display text-title text-ink-900 dark:text-ink-100"
        >
          {entry.student_name}
        </h3>
        <div className="mt-2 flex items-center gap-2">
          <StatusBadge status={entry.status} />
          {entry.method === "face" && entry.confidence && (
            <span data-numeric className="font-mono text-[11px] text-ink-400">
              {(entry.confidence * 100).toFixed(0)}% match
            </span>
          )}
          {!entry.face_enrolled && (
            <span className="text-[11px] text-ink-400">no face data</span>
          )}
        </div>

        <div className="mt-5 grid grid-cols-2 gap-2">
          {options.map((o) => (
            <Button
              key={o.status}
              variant={entry.status === o.status ? o.variant : "secondary"}
              size="sm"
              loading={busy === o.status}
              onClick={() => onMark(entry.student_id, o.status)}
            >
              {o.label}
            </Button>
          ))}
        </div>

        <Button variant="ghost" size="sm" className="mt-3 w-full" onClick={onClose}>
          Close
        </Button>
      </Card>
    </div>
  );
}

export default function AttendanceScreen() {
  const [params] = useSearchParams();
  const offeringId = Number(params.get("offering")) || null;

  const camera = useCamera();
  const [session, setSession] = useState(null);
  const [roster, setRoster] = useState(null);
  const [faces, setFaces] = useState([]);
  const [frameSize, setFrameSize] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [selected, setSelected] = useState(null);
  const [closing, setClosing] = useState(false);
  const [summary, setSummary] = useState(null);

  const runningRef = useRef(false);
  const inFlight = useRef(false);
  // MEMORY LEAK FIX: the loop scheduled setTimeout without keeping the id, so
  // on unmount the final callback still fired -- calling setState on a dead
  // component and, worse, issuing one more upload after the user navigated
  // away. Storing the id lets cleanup cancel it.
  const timerRef = useRef(null);

  const recentlyMarked = useRecentlyMarked(roster?.entries ?? []);

  useEffect(() => {
    document.title = "Take attendance — Register";
  }, []);

  const refreshRoster = useCallback(async (sessionId) => {
    try {
      setRoster(await sessionApi.roster(sessionId));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  async function openSession() {
    if (!offeringId) {
      setError("No class selected. Open this page from My classes.");
      return;
    }
    setBusy("open");
    setError(null);
    try {
      const s = await sessionApi.open({ offering_id: offeringId, room: null });
      setSession(s);
      await refreshRoster(s.id);
    } catch (err) {
      // A page refresh loses the in-memory session even though the backend
      // session is still open. Rejoin the session reported by the API instead
      // of asking the user to close a valid attendance session.
      const detail = err?.response?.data?.detail ?? "";
      const existingSessionId = detail.match(
        /open session already exists for this offering \(session (\d+)/
      )?.[1];
      if (err?.response?.status === 409 && existingSessionId) {
        const id = Number(existingSessionId);
        setSession({
          id,
          offering_id: offeringId,
          started_at: new Date().toISOString(),
        });
        await refreshRoster(id);
        setError(null);
        return;
      }
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  /* ---- The self-scheduling capture loop ---- */
  const captureLoop = useCallback(async () => {
    if (!runningRef.current || !session) return;

    if (!inFlight.current) {
      inFlight.current = true;
      setScanning(true);
      const started = Date.now();
      try {
        const blob = await camera.capture();
        if (blob) {
          const video = camera.videoRef.current;
          if (video) {
            setFrameSize({ width: video.videoWidth, height: video.videoHeight });
          }
          const result = await sessionApi.markFace(session.id, blob);
          setFaces(result.faces);
          setError(null);
          // Refresh immediately after a successful recognition. The backend
          // remains idempotent, so repeated frames cannot create duplicates,
          // while a newly marked student is reflected in the register count
          // without waiting for another user action.
          if (
            result.newly_marked > 0 ||
            result.faces.some((face) => face.marked || face.already_marked)
          ) {
            await refreshRoster(session.id);
          }
        }
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        inFlight.current = false;
        setScanning(false);
      }

      // Wait only the REMAINING time, so a slow server slows the loop instead
      // of queueing behind it.
      const elapsed = Date.now() - started;
      const wait = Math.max(0, CAPTURE_INTERVAL_MS - elapsed);
      timerRef.current = setTimeout(captureLoop, wait);
      return;
    }
    timerRef.current = setTimeout(captureLoop, CAPTURE_INTERVAL_MS);
  }, [camera, session, refreshRoster]);

  async function startCamera() {
    const ok = await camera.start();
    if (!ok) return;
    runningRef.current = true;
    captureLoop();
  }

  function stopCamera() {
    runningRef.current = false;
    clearTimeout(timerRef.current);
    timerRef.current = null;
    camera.stop();
    setFaces([]);
  }

  // Stop the loop when leaving the page, or the interval keeps firing against
  // an unmounted component.
  useEffect(() => {
    return () => {
      runningRef.current = false;
      // Cancel the pending tick. Without this the loop fires once more after
      // unmount, uploading a frame for a session the user has left.
      clearTimeout(timerRef.current);
    };
  }, []);

  async function markManually(studentId, status) {
    setBusy(status);
    try {
      await sessionApi.markManual(session.id, studentId, status);
      await refreshRoster(session.id);
      setSelected(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function closeSession() {
    setClosing(true);
    try {
      stopCamera();
      setSummary(await sessionApi.close(session.id));
      setSession(null);
      setRoster(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setClosing(false);
    }
  }

  /* ---- Render ---- */

  if (summary) {
    return (
      <Page className="max-w-2xl">
        <PageHeader eyebrow="Class finished" title="Attendance recorded" />
        <Card className="p-6">
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
            <div>
              <p className="eyebrow">Present</p>
              <p data-numeric className="mt-1.5 font-display text-3xl text-present">
                {summary.present_count}
              </p>
            </div>
            <div>
              <p className="eyebrow">Late</p>
              <p data-numeric className="mt-1.5 font-display text-3xl text-late">
                {summary.late_count}
              </p>
            </div>
            <div>
              <p className="eyebrow">Absent</p>
              <p data-numeric className="mt-1.5 font-display text-3xl text-absent">
                {summary.absent_count}
              </p>
            </div>
            <div>
              <p className="eyebrow">To review</p>
              <p data-numeric className="mt-1.5 font-display text-3xl text-ink-500">
                {summary.review_count}
              </p>
            </div>
          </div>
          <p className="mt-6 text-sm text-ink-500 dark:text-ink-400">
            {summary.auto_absent_created} student
            {summary.auto_absent_created === 1 ? " was" : "s were"} marked absent
            automatically at close.
          </p>
          <Button className="mt-6" onClick={() => setSummary(null)}>
            Start another class
          </Button>
        </Card>
      </Page>
    );
  }

  if (!session) {
    return (
      <Page className="max-w-2xl">
        <PageHeader eyebrow="Attendance" title="Start a class" />
        {error && (
          <Alert variant="error" className="mb-5">
            {error}
          </Alert>
        )}
        <Card>
          <EmptyState
            icon={Users}
            title={offeringId ? "Ready when you are" : "No class selected"}
            description={
              offeringId
                ? "Opening a session starts a new register for today. Students are marked as the camera recognises them, and anyone still missing is marked absent when you close it."
                : "Go to My classes and choose the class you are about to teach."
            }
            action={
              offeringId ? (
                <Button size="lg" icon={Play} loading={busy === "open"} onClick={openSession}>
                  Open session
                </Button>
              ) : null
            }
          />
        </Card>
      </Page>
    );
  }

  const marked = roster?.marked_count ?? 0;
  const total = roster?.enrolled_count ?? 0;

  return (
    <Page>
      <PageHeader
        eyebrow="Live session"
        title="Take attendance"
        description={`Session ${session.id} · opened ${new Date(
          session.started_at
        ).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
        action={
          <Button variant="secondary" icon={Square} loading={closing} onClick={closeSession}>
            Close session
          </Button>
        }
      />

      {error && (
        <Alert variant="error" className="mb-5">
          {error}
        </Alert>
      )}

      {/* Camera left, register right. Both visible at once, deliberately. */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div>
          <CameraStage
            videoRef={camera.videoRef}
            active={camera.active}
            starting={camera.starting}
            error={camera.error}
            faces={faces}
            frameSize={frameSize}
            scanning={scanning}
            onStart={startCamera}
            onStop={stopCamera}
          />
        </div>

        <Card className="flex flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-ink-900/5 px-5 py-4 dark:border-white/5">
            <div>
              <p className="eyebrow mb-1">Register</p>
              <p className="font-display text-title text-ink-900 dark:text-ink-100">
                <span data-numeric className="font-mono">
                  {marked}
                </span>
                <span className="text-ink-400"> / {total} marked</span>
              </p>
            </div>
            <span className="text-[11px] text-ink-400">Tap a tile to change</span>
          </div>

          <div className="flex-1 p-5">
            {roster ? (
              <RegisterGrid
                entries={roster.entries}
                recentlyMarked={recentlyMarked}
                onSelect={setSelected}
              />
            ) : (
              <EmptyState title="Loading the register…" />
            )}
          </div>
        </Card>
      </div>

      <MarkSheet
        entry={selected}
        busy={busy}
        onClose={() => setSelected(null)}
        onMark={markManually}
      />
    </Page>
  );
}
