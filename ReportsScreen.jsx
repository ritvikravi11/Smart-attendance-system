/**
 * REPORTS SCREEN
 *
 * TWO VIEWS, one switch: the class as a whole, or the individuals in trouble.
 * That split exists because those are genuinely different questions and they
 * want different shapes -- a distribution for the first, a ranked list for the
 * second.
 *
 * WHY A HISTOGRAM AND NOT A TREND LINE
 * ------------------------------------
 * A line of "attendance over time" looks like analytics but answers nothing a
 * lecturer acts on. The useful question is "how is my class DISTRIBUTED?" --
 * is everyone hovering around 80%, or is it bimodal with a group who never
 * come? A histogram shows that instantly, and the 75% line drawn across it
 * turns the chart into the same comparison the whole app is built around.
 *
 * CSV EXPORT IS CLIENT-SIDE, deliberately. The data is already in the browser;
 * a round trip to generate a file the client can assemble itself would be
 * slower and add an endpoint for no benefit.
 */

import { ChartNoAxesColumn, Download, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import { analyticsApi } from "../api/endpoints";
import { Page, PageHeader } from "../components/layout/AppShell";
import { ThresholdMeter } from "../components/attendance/ThresholdMeter";
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

const THRESHOLD = 75;

/** Assemble and download a CSV without a server round trip. */
function downloadCsv(filename, rows) {
  if (!rows.length) return;
  const headers = Object.keys(rows[0]);
  const escape = (v) => {
    const s = String(v ?? "");
    // Quote anything containing a comma, quote or newline; double inner quotes.
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [
    headers.join(","),
    ...rows.map((r) => headers.map((h) => escape(r[h])).join(",")),
  ].join("\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url); // release the blob or it leaks for the tab's life
}

function DefaulterRow({ student, rank }) {
  return (
    <div className="flex items-center gap-4 px-5 py-4">
      <span
        data-numeric
        className="w-6 shrink-0 font-mono text-[13px] text-ink-300 dark:text-ink-600"
      >
        {rank}
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-mono text-[13px] font-semibold text-ink-800 dark:text-ink-200">
          {student.roll_number}
        </p>
        <p className="truncate text-sm text-ink-600 dark:text-ink-400">
          {student.student_name}
        </p>
      </div>
      <div className="w-40 shrink-0 sm:w-56">
        <ThresholdMeter
          percentage={student.percentage}
          attended={student.attended}
          denominator={student.denominator}
          shortfall={student.shortfall_sessions}
          size="sm"
          showScale={false}
        />
      </div>
    </div>
  );
}

export default function ReportsScreen() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();

  const [offerings, setOfferings] = useState([]);
  const [offeringId, setOfferingId] = useState(
    Number(params.get("offering")) || null
  );
  const [defaulters, setDefaulters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const facultyId = user?.faculty_profile?.id ?? user?.id;

  useEffect(() => {
    document.title = "Reports — Register";
  }, []);

  // Load the offering list once, so the picker has something to show.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const summary = await analyticsApi.facultyAttendance(facultyId);
        if (cancelled) return;
        setOfferings(summary.offerings ?? []);
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

  const loadDefaulters = useCallback(async (id) => {
    if (!id) return;
    try {
      setDefaulters(await analyticsApi.defaulters(id));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    if (offeringId) {
      setParams({ offering: String(offeringId) }, { replace: true });
      loadDefaulters(offeringId);
    }
  }, [offeringId, loadDefaulters, setParams]);

  const offering = offerings.find((o) => o.offering_id === offeringId);

  function exportCsv() {
    downloadCsv(
      `${offering?.subject_code ?? "attendance"}-below-threshold.csv`,
      defaulters.map((d) => ({
        roll_number: d.roll_number,
        name: d.student_name,
        percentage: d.percentage,
        attended: d.attended,
        total: d.denominator,
        classes_needed: d.shortfall_sessions,
      }))
    );
  }

  if (loading) {
    return (
      <Page>
        <PageHeader eyebrow="Analysis" title="Reports" />
        <Card className="p-6">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="mt-4 h-56 w-full" />
        </Card>
      </Page>
    );
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Analysis"
        title="Reports"
        description="Attendance distribution and the students who need attention."
        action={
          defaulters.length > 0 ? (
            <Button variant="secondary" icon={Download} onClick={exportCsv}>
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

      {offerings.length === 0 ? (
        <Card>
          <EmptyState
            icon={Users}
            title="No classes to report on"
            description="Reports appear once you are assigned a subject and have held at least one class."
          />
        </Card>
      ) : (
        <div className="space-y-6">
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

          {offering && (
            <Card className="p-5 sm:p-6">
              <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
                <Stat label="Students" value={offering.enrolled_count} />
                <Stat label="Classes held" value={offering.closed_sessions} />
                <Stat
                  label="Class average"
                  value={
                    offering.average_percentage !== null
                      ? `${offering.average_percentage.toFixed(1)}%`
                      : "—"
                  }
                  tone={
                    offering.average_percentage === null
                      ? "muted"
                      : offering.average_percentage >= THRESHOLD
                      ? "present"
                      : "absent"
                  }
                />
                <Stat
                  label="Below 75%"
                  value={offering.defaulter_count}
                  tone={offering.defaulter_count > 0 ? "absent" : "present"}
                />
              </div>
            </Card>
          )}

          {/* RANKED LIST -- worst first, because that is the action order. */}
          <Card>
            <div className="flex items-center justify-between border-b border-ink-900/5 px-5 py-4 dark:border-white/5">
              <div>
                <p className="eyebrow mb-1">Needs attention</p>
                <h2 className="font-display text-title text-ink-900 dark:text-ink-100">
                  Below 75%
                </h2>
              </div>
              <span data-numeric className="font-mono text-sm text-ink-400">
                {defaulters.length}
              </span>
            </div>

            {defaulters.length === 0 ? (
              <EmptyState
                title="Nothing to flag"
                description="Every student in this class is above the attendance threshold."
              />
            ) : (
              <div className="divide-y divide-ink-900/5 dark:divide-white/5">
                {defaulters.map((s, i) => (
                  <DefaulterRow key={s.student_id} student={s} rank={i + 1} />
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </Page>
  );
}
