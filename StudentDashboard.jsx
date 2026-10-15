/**
 * STUDENT DASHBOARD
 *
 * THE PAGE HAS ONE JOB: answer "am I safe?" before the student scrolls.
 *
 * So the hero is the overall threshold meter, not a row of stat cards. A
 * student who is at 82% needs to know that and leave; a student at 68% needs
 * to know exactly which subject is dragging them down and how many classes
 * will fix it.
 *
 * SUBJECTS ARE SORTED WORST-FIRST, deliberately. Alphabetical order buries the
 * one row that matters. The page should put the problem at the top.
 */

import { BookOpen, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { errorMessage } from "../api/client";
import { analyticsApi } from "../api/endpoints";
import { Page, PageHeader } from "../components/layout/AppShell";
import { ThresholdMeter } from "../components/attendance/ThresholdMeter";
import {
  Alert,
  Card,
  EmptyState,
  Skeleton,
  StatusBadge,
  cx,
} from "../components/ui/primitives";
import { useAuth } from "../context/AuthContext";

/** One subject row: identity on the left, the meter doing the work on the right. */
function SubjectRow({ subject }) {
  return (
    <div className="grid gap-4 px-5 py-5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] sm:items-center">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[13px] font-semibold text-ink-800 dark:text-ink-200">
            {subject.subject_code}
          </span>
          <span className="rounded bg-ink-900/5 px-1.5 py-0.5 font-mono text-[10px] text-ink-500 dark:bg-white/5 dark:text-ink-400">
            {subject.section}
          </span>
        </div>
        <p className="mt-1 truncate text-sm font-medium text-ink-900 dark:text-ink-100">
          {subject.subject_name}
        </p>
        <p className="mt-0.5 truncate text-[13px] text-ink-400">
          {subject.faculty_name}
        </p>
      </div>

      <div className="sm:pl-4">
        <ThresholdMeter
          percentage={subject.percentage}
          attended={subject.attended}
          denominator={subject.denominator}
          shortfall={subject.shortfall_sessions}
          size="sm"
          showScale={false}
        />
        {/* Status counts. Uses the same StatusBadge vocabulary as everywhere
            else, so "present" looks identical here and on the roster. */}
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          {subject.present > 0 && (
            <span className="flex items-center gap-1 text-[11px] text-ink-500 dark:text-ink-400">
              <StatusBadge status="present" size="sm" />
              <span data-numeric className="font-mono">{subject.present}</span>
            </span>
          )}
          {subject.late > 0 && (
            <span className="flex items-center gap-1 text-[11px] text-ink-500 dark:text-ink-400">
              <StatusBadge status="late" size="sm" />
              <span data-numeric className="font-mono">{subject.late}</span>
            </span>
          )}
          {subject.absent > 0 && (
            <span className="flex items-center gap-1 text-[11px] text-ink-500 dark:text-ink-400">
              <StatusBadge status="absent" size="sm" />
              <span data-numeric className="font-mono">{subject.absent}</span>
            </span>
          )}
          {subject.excused > 0 && (
            <span className="flex items-center gap-1 text-[11px] text-ink-500 dark:text-ink-400">
              <StatusBadge status="excused" size="sm" />
              <span data-numeric className="font-mono">{subject.excused}</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-6">
      <Card className="p-6">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="mt-4 h-10 w-40" />
        <Skeleton className="mt-4 h-2.5 w-full" />
      </Card>
      <Card>
        {[0, 1, 2].map((i) => (
          <div key={i} className="border-b border-ink-900/5 p-5 last:border-0 dark:border-white/5">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="mt-3 h-2 w-full" />
          </div>
        ))}
      </Card>
    </div>
  );
}

export default function StudentDashboard() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const studentId = user?.student_profile?.id ?? user?.id;

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setData(await analyticsApi.studentAttendance(studentId));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [studentId]);

  useEffect(() => {
    document.title = "My attendance — Register";
    load();
  }, [load]);

  if (loading) {
    return (
      <Page>
        <PageHeader eyebrow="My record" title="Attendance" />
        <LoadingState />
      </Page>
    );
  }

  if (error) {
    return (
      <Page>
        <PageHeader eyebrow="My record" title="Attendance" />
        <Alert variant="error" title="Could not load your attendance">
          {error}
        </Alert>
      </Page>
    );
  }

  // Worst first. The problem belongs at the top of the page.
  const subjects = [...(data?.subjects ?? [])].sort((a, b) => {
    if (a.percentage === null) return 1;
    if (b.percentage === null) return -1;
    return a.percentage - b.percentage;
  });

  const atRisk = subjects.filter((s) => s.is_below_threshold);

  return (
    <Page>
      <PageHeader
        eyebrow={data?.roll_number}
        title="Attendance"
        description={`${data?.student_name} · ${subjects.length} ${
          subjects.length === 1 ? "subject" : "subjects"
        } this semester`}
      />

      <div className="space-y-6">
        {/* HERO: the one question the page exists to answer. */}
        <Card className="p-6 sm:p-7">
          <p className="eyebrow mb-4">Overall</p>
          <ThresholdMeter
            percentage={data?.overall_percentage}
            attended={data?.overall_attended}
            denominator={data?.overall_denominator}
            size="lg"
          />
        </Card>

        {/* A warning only when there is something to warn about. A permanent
            banner that says "you're fine" is noise people learn to ignore. */}
        {atRisk.length > 0 && (
          <Alert variant="warning" title={`${atRisk.length} subject${atRisk.length > 1 ? "s" : ""} below 75%`}>
            {atRisk.map((s) => s.subject_code).join(", ")} need attention. Each row
            below shows how many consecutive classes will bring it back up.
          </Alert>
        )}

        <Card>
          <div className="flex items-center justify-between border-b border-ink-900/5 px-5 py-4 dark:border-white/5">
            <h2 className="font-display text-title text-ink-900 dark:text-ink-100">
              By subject
            </h2>
            <span className="text-[11px] text-ink-400">Lowest first</span>
          </div>

          {subjects.length === 0 ? (
            <EmptyState
              icon={BookOpen}
              title="No subjects yet"
              description="Once your department enrols you in subjects, your attendance for each will appear here."
            />
          ) : (
            <div className="divide-y divide-ink-900/5 dark:divide-white/5">
              {subjects.map((s) => (
                <SubjectRow key={s.offering_id} subject={s} />
              ))}
            </div>
          )}
        </Card>
      </div>
    </Page>
  );
}
