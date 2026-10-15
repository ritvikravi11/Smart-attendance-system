/**
 * FACULTY DASHBOARD
 *
 * THE PAGE'S JOB: get a lecturer from "I'm about to teach" to "attendance is
 * running" in one click, and otherwise tell them which of their classes needs
 * attention.
 *
 * So the primary action on every offering card is "Take attendance". Everything
 * else on the card is context for deciding whether to look closer.
 *
 * WHY CLASS AVERAGE AND DEFAULTER COUNT, and not a chart
 * ------------------------------------------------------
 * A trend line looks impressive and answers nothing a lecturer acts on. Two
 * numbers do: how the class is doing overall, and how many individuals are in
 * trouble. The second is a link, because it is the number you click.
 */

import { ClipboardList, ScanFace, TriangleAlert, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { errorMessage } from "../api/client";
import { analyticsApi, recognitionApi } from "../api/endpoints";
import { Page, PageHeader } from "../components/layout/AppShell";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  Skeleton,
  Stat,
  cx,
} from "../components/ui/primitives";
import { useAuth } from "../context/AuthContext";

/**
 * One offering. The card IS the entry point to teaching that class, so the
 * whole thing is built around its primary action.
 */
function OfferingCard({ offering }) {
  const avg = offering.average_percentage;
  const hasData = avg !== null && avg !== undefined;
  const healthy = hasData && avg >= 75;

  return (
    <Card className="flex flex-col overflow-hidden">
      <div className="flex-1 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[13px] font-semibold text-ink-800 dark:text-ink-200">
                {offering.subject_code}
              </span>
              <span className="rounded bg-ink-900/5 px-1.5 py-0.5 font-mono text-[10px] text-ink-500 dark:bg-white/5 dark:text-ink-400">
                {offering.section}
              </span>
            </div>
            <h3 className="mt-1.5 truncate font-display text-base font-semibold text-ink-900 dark:text-ink-100">
              {offering.subject_name}
            </h3>
            <p className="mt-0.5 font-mono text-[11px] text-ink-400">
              {offering.academic_year}
            </p>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-3 gap-4">
          <Stat
            label="Students"
            value={offering.enrolled_count}
            className="min-w-0"
          />
          <Stat
            label="Classes"
            value={offering.closed_sessions}
            sub={
              offering.cancelled_sessions > 0
                ? `${offering.cancelled_sessions} cancelled`
                : undefined
            }
            className="min-w-0"
          />
          <Stat
            label="Average"
            value={hasData ? `${avg.toFixed(0)}%` : "—"}
            tone={!hasData ? "muted" : healthy ? "present" : "absent"}
            className="min-w-0"
          />
        </div>

        {offering.defaulter_count > 0 && (
          <Link
            to={`/reports?offering=${offering.offering_id}&view=defaulters`}
            className="mt-4 flex items-center gap-2 rounded-lg bg-absent-soft px-3 py-2 text-[13px] font-medium text-absent-ink transition-colors hover:bg-absent/15 dark:bg-absent/10 dark:text-absent"
          >
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span data-numeric className="font-mono font-semibold">
              {offering.defaulter_count}
            </span>
            {offering.defaulter_count === 1 ? "student" : "students"} below 75%
          </Link>
        )}
      </div>

      {/* Primary action, given its own band so it reads as the thing to do. */}
      <div className="border-t border-ink-900/5 bg-ink-900/[0.02] p-3 dark:border-white/5 dark:bg-white/[0.02]">
        {/* Client-side navigation. Previously this used
            window.location.href, which reloaded the whole application. */}
        <Button
          as={Link}
          to={`/attendance?offering=${offering.offering_id}`}
          variant="primary"
          size="sm"
          icon={ScanFace}
          className="w-full"
        >
          Take attendance
        </Button>
      </div>
    </Card>
  );
}

export default function FacultyDashboard() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const facultyId = user?.faculty_profile?.id ?? user?.id;

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const [summary, hp] = await Promise.allSettled([
        analyticsApi.facultyAttendance(facultyId),
        recognitionApi.health(),
      ]);
      if (summary.status === "fulfilled") setData(summary.value);
      else setError(errorMessage(summary.reason));
      // Health is advisory -- a failure here must not blank the page.
      if (hp.status === "fulfilled") setHealth(hp.value);
    } finally {
      setLoading(false);
    }
  }, [facultyId]);

  useEffect(() => {
    document.title = "My classes — Register";
    load();
  }, [load]);

  if (loading) {
    return (
      <Page>
        <PageHeader eyebrow="Teaching" title="My classes" />
        <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Card key={i} className="p-5">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="mt-3 h-5 w-40" />
              <Skeleton className="mt-6 h-10 w-full" />
            </Card>
          ))}
        </div>
      </Page>
    );
  }

  const offerings = data?.offerings ?? [];

  return (
    <Page>
      <PageHeader
        eyebrow="Teaching"
        title="My classes"
        description={
          data
            ? `${data.faculty_name} · ${data.total_sessions_conducted} classes conducted · ${data.total_students_taught} students`
            : undefined
        }
      />

      <div className="space-y-6">
        {error && (
          <Alert variant="error" title="Could not load your classes">
            {error}
          </Alert>
        )}

        {/* Recognition status. Shown ONLY when something is wrong -- a green
            "all systems normal" banner is noise that trains people to ignore
            the banner area entirely. */}
        {health && !health.model_loaded && (
          <Alert variant="warning" title="Face recognition is unavailable">
            The model did not load, so automatic marking is off. You can still
            take attendance by marking students by hand.
          </Alert>
        )}
        {health?.model_loaded && health.index_size === 0 && (
          <Alert variant="warning" title="No students have face data yet">
            Recognition will report every face as unknown until an administrator
            enrols student photographs. Manual marking works now.
          </Alert>
        )}

        {offerings.length === 0 ? (
          <Card>
            <EmptyState
              icon={ClipboardList}
              title="No classes assigned"
              description="Once your department assigns you to a subject, it will appear here and you can start taking attendance."
            />
          </Card>
        ) : (
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
            {offerings.map((o) => (
              <OfferingCard key={o.offering_id} offering={o} />
            ))}
          </div>
        )}
      </div>
    </Page>
  );
}
