/**
 * ANALYTICS SCREEN
 *
 * Five charts, one request.
 *
 * WHY ONE REQUEST
 * ---------------
 * These charts must agree with each other. Fetched separately, a session could
 * close between call two and call four, so the daily chart would include a
 * session the student percentages do not -- and the page would silently
 * contradict itself. One bundled endpoint gives one consistent snapshot.
 *
 * READING ORDER, top to bottom, is deliberate:
 *   summary   → the headline numbers
 *   daily     → what just happened
 *   monthly   → where it is heading
 *   students  → how the class is spread
 *   absentees → who to talk to
 *
 * It moves from "what" to "so what" to "who", which is the order a lecturer
 * actually thinks in.
 */

import { ChartNoAxesColumn, Download } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import { analyticsApi } from "../api/endpoints";
import {
  DailyAttendanceChart,
  MonthlyTrendChart,
  StudentPercentChart,
  SubjectPercentChart,
  TopAbsenteesChart,
} from "../components/charts/AttendanceCharts";
import { Page, PageHeader } from "../components/layout/AppShell";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  Select,
  Skeleton,
  Stat,
} from "../components/ui/primitives";
import { useAuth } from "../context/AuthContext";

/**
 * Chart wrapper.
 *
 * The fixed-height div is not styling -- it is REQUIRED. Chart.js runs with
 * `maintainAspectRatio: false`, which makes the parent's height authoritative.
 * Without an explicit height the container collapses to zero and the chart
 * disappears with no error.
 *
 * The caption under each title states what the chart answers. A chart that
 * needs explaining in a caption is usually the wrong chart -- but a chart whose
 * QUESTION is stated gets read correctly by someone who has never seen it.
 */
function ChartCard({ eyebrow, title, question, height = 260, action, children }) {
  return (
    <Card className="p-5 sm:p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="eyebrow mb-1">{eyebrow}</p>
          <h2 className="font-display text-title text-ink-900 dark:text-ink-100">
            {title}
          </h2>
          <p className="mt-1 text-[13px] text-ink-500 dark:text-ink-400">{question}</p>
        </div>
        {action}
      </div>
      <div style={{ height }}>{children}</div>
    </Card>
  );
}

function downloadCsv(filename, rows) {
  if (!rows.length) return;
  const headers = Object.keys(rows[0]);
  const escape = (v) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [
    headers.join(","),
    ...rows.map((r) => headers.map((h) => escape(r[h])).join(",")),
  ].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8;" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function AnalyticsScreen() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();

  const [offerings, setOfferings] = useState([]);
  const [offeringId, setOfferingId] = useState(Number(params.get("offering")) || null);
  const [dashboard, setDashboard] = useState(null);
  const [subjects, setSubjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const facultyId = user?.faculty_profile?.id ?? user?.id;

  useEffect(() => {
    document.title = "Analytics — Register";
  }, []);

  // Offering list + subject comparison load once; both are faculty-wide.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [summary, subjectRates] = await Promise.all([
          analyticsApi.facultyAttendance(facultyId),
          analyticsApi.subjectPercentages(facultyId),
        ]);
        if (cancelled) return;
        setOfferings(summary.offerings ?? []);
        setSubjects(subjectRates ?? []);
        if (!offeringId && summary.offerings?.length) {
          setOfferingId(summary.offerings[0].offering_id);
        }
      } catch (err) {
        if (!cancelled) setError(errorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [facultyId]);

  const loadDashboard = useCallback(async (id) => {
    if (!id) return;
    try {
      setDashboard(await analyticsApi.offeringDashboard(id));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    if (offeringId) {
      setParams({ offering: String(offeringId) }, { replace: true });
      loadDashboard(offeringId);
    }
  }, [offeringId, loadDashboard, setParams]);

  if (loading) {
    return (
      <Page>
        <PageHeader eyebrow="Analysis" title="Analytics" />
        <div className="grid gap-5 lg:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Card key={i} className="p-6">
              <Skeleton className="h-4 w-28" />
              <Skeleton className="mt-4 h-48 w-full" />
            </Card>
          ))}
        </div>
      </Page>
    );
  }

  if (!offerings.length) {
    return (
      <Page>
        <PageHeader eyebrow="Analysis" title="Analytics" />
        <Card>
          <EmptyState
            icon={ChartNoAxesColumn}
            title="No classes to analyse"
            description="Charts appear once you are assigned a subject and have held at least one class."
          />
        </Card>
      </Page>
    );
  }

  const summary = dashboard?.summary;

  return (
    <Page>
      <PageHeader
        eyebrow="Analysis"
        title="Analytics"
        description="How this class is attending, and who needs following up."
        action={
          dashboard?.students?.length ? (
            <Button
              variant="secondary"
              icon={Download}
              onClick={() =>
                downloadCsv(
                  `attendance-${offeringId}.csv`,
                  dashboard.students.map((s) => ({
                    roll_number: s.roll_number,
                    name: s.student_name,
                    present: s.present,
                    late: s.late,
                    absent: s.absent,
                    excused: s.excused,
                    percentage: s.percentage ?? "",
                  }))
                )
              }
            >
              Export CSV
            </Button>
          ) : null
        }
      />

      {error && (
        <Alert variant="error" className="mb-5">
          {error}
        </Alert>
      )}

      <div className="space-y-5">
        <div className="max-w-xs">
          <Select
            label="Class"
            value={offeringId ?? ""}
            onChange={(e) => setOfferingId(Number(e.target.value))}
            options={offerings.map((o) => ({
              value: o.offering_id,
              label: `${o.subject_code} · Section ${o.section}`,
            }))}
          />
        </div>

        {/* Headline numbers first. Someone who only reads the top of the page
            still leaves knowing whether the class is healthy. */}
        {summary && (
          <Card className="p-5 sm:p-6">
            <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
              <Stat label="Students" value={summary.enrolled} />
              <Stat
                label="Class average"
                value={
                  summary.class_average !== null ? `${summary.class_average}%` : "—"
                }
                tone={
                  summary.class_average === null
                    ? "muted"
                    : summary.class_average >= summary.threshold
                    ? "present"
                    : "absent"
                }
              />
              <Stat
                label="Below 75%"
                value={summary.below_threshold}
                tone={summary.below_threshold > 0 ? "absent" : "present"}
              />
              <Stat label="Classes held" value={dashboard?.daily?.length ?? 0} />
            </div>
          </Card>
        )}

        <ChartCard
          eyebrow="Per class"
          title="Daily attendance"
          question="Was today unusual? Bar height shows how many were in the room; the split shows the mix."
          height={280}
        >
          <DailyAttendanceChart data={dashboard?.daily ?? []} />
        </ChartCard>

        <div className="grid gap-5 lg:grid-cols-2">
          <ChartCard
            eyebrow="Over time"
            title="Monthly trend"
            question="Is the semester improving or slipping? Points below the line are months the class averaged under 75%."
          >
            <MonthlyTrendChart data={dashboard?.monthly ?? []} />
          </ChartCard>

          <ChartCard
            eyebrow="Population"
            title="Student distribution"
            question="Is everyone near the average, or is the class split? Red bars are students below the line."
          >
            <StudentPercentChart students={dashboard?.students ?? []} />
          </ChartCard>
        </div>

        <div className="grid gap-5 lg:grid-cols-2">
          <ChartCard
            eyebrow="Across classes"
            title="Subject comparison"
            question="Which of your classes is struggling most?"
          >
            <SubjectPercentChart subjects={subjects} />
          </ChartCard>

          <ChartCard
            eyebrow="Follow up"
            title="Most classes missed"
            question="Ranked by classes missed, not percentage — a different question from who is below the line."
          >
            <TopAbsenteesChart absentees={dashboard?.top_absentees ?? []} />
          </ChartCard>
        </div>
      </div>
    </Page>
  );
}
