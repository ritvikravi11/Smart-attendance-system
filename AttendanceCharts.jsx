/**
 * THE FIVE CHARTS
 * ===============
 * Each one answers a specific question. If a chart cannot be described in one
 * sentence starting "this tells you...", it should not exist.
 *
 *   1. DailyAttendanceChart   — was today unusual?
 *   2. MonthlyTrendChart      — is the semester getting better or worse?
 *   3. StudentPercentChart    — how is the class distributed?
 *   4. SubjectPercentChart    — which of my classes is struggling?
 *   5. TopAbsenteesChart      — who is not turning up?
 *
 * A NOTE ON CHART TYPE SELECTION, which is where most dashboards go wrong:
 *   comparison across categories → bar
 *   change over time             → line
 *   distribution of a population → histogram (bar over binned ranges)
 *
 * Pie charts appear nowhere. Humans compare angles badly, and every question
 * here is a comparison of magnitudes, which bars do better.
 */

import { useMemo } from "react";
import { Bar, Line } from "react-chartjs-2";

import { useTheme } from "../../context/ThemeContext";
import {
  CHART_COLORS,
  baseOptions,
  percentScale,
  readTheme,
  thresholdLinePlugin,
} from "./setup";

const THRESHOLD = 75;

/** Shared empty state, so no chart renders an empty axis with no explanation. */
function NoData({ message }) {
  return (
    <div className="flex h-full items-center justify-center text-center">
      <p className="max-w-xs text-[13px] text-ink-400">{message}</p>
    </div>
  );
}

/* ==================================================================== *
 * 1. DAILY ATTENDANCE  —  stacked bars, one per session
 *
 * QUESTION: was today unusual?
 *
 * WHY STACKED BARS AND NOT A LINE
 * A line of "attendance rate" hides the class size. Sixteen present out of
 * twenty and eight out of ten are both 80%, but they are very different days.
 * Stacked bars show the RATE (the proportion) and the VOLUME (the bar height)
 * at once.
 *
 * WHY THE X-AXIS IS SESSIONS, NOT CALENDAR DAYS
 * A calendar axis emits a bar for every weekend and holiday. Those are not
 * zero-attendance days -- they are no-class days, and drawing them as zeros
 * produces a sawtooth that reads as collapsing attendance. Only actual
 * sessions get a bar.
 * ==================================================================== */

export function DailyAttendanceChart({ data = [] }) {
  const { theme: mode } = useTheme();

  const { chartData, options } = useMemo(() => {
    const theme = readTheme();
    const base = baseOptions(theme);

    return {
      chartData: {
        labels: data.map((d) =>
          new Date(d.date).toLocaleDateString([], { day: "2-digit", month: "short" })
        ),
        datasets: [
          {
            label: "Present",
            data: data.map((d) => d.present),
            backgroundColor: CHART_COLORS.present,
            borderRadius: { topLeft: 0, topRight: 0 },
            stack: "a",
          },
          {
            label: "Late",
            data: data.map((d) => d.late),
            backgroundColor: CHART_COLORS.late,
            stack: "a",
          },
          {
            label: "Absent",
            data: data.map((d) => d.absent),
            backgroundColor: CHART_COLORS.absent,
            // Rounded on the top segment only, so the stack reads as one bar
            // rather than three floating blocks.
            borderRadius: { topLeft: 3, topRight: 3 },
            stack: "a",
          },
        ],
      },
      options: {
        ...base,
        plugins: {
          ...base.plugins,
          legend: { ...base.plugins.legend, display: true, position: "bottom" },
          tooltip: {
            ...base.plugins.tooltip,
            callbacks: {
              // The rate is the number people actually want, so it goes in the
              // tooltip footer rather than making them do the arithmetic.
              footer: (items) => {
                const point = data[items[0].dataIndex];
                return point?.rate != null ? `Attendance ${point.rate}%` : "";
              },
            },
          },
        },
        scales: {
          x: { ...base.scales.x, stacked: true },
          y: {
            ...base.scales.y,
            stacked: true,
            ticks: { ...base.scales.y.ticks, precision: 0 },
          },
        },
      },
    };
  }, [data, mode]);

  if (!data.length) {
    return <NoData message="No classes have been held yet. Bars appear here once you close your first session." />;
  }
  return <Bar data={chartData} options={options} />;
}

/* ==================================================================== *
 * 2. MONTHLY TREND  —  line with the threshold marked
 *
 * QUESTION: is the semester getting better or worse?
 *
 * WHY A LINE HERE BUT BARS ABOVE
 * Time series with a meaningful direction want a line -- the slope IS the
 * information. Daily data is too noisy for that (one exam day tanks a point),
 * but monthly aggregates smooth it into a real trend.
 *
 * `spanGaps: false` is deliberate. If a month has no classes, the line BREAKS
 * rather than drawing a straight segment across the gap. A connected line
 * would invent data for a month that never happened.
 * ==================================================================== */

export function MonthlyTrendChart({ data = [] }) {
  const { theme: mode } = useTheme();

  const { chartData, options } = useMemo(() => {
    const theme = readTheme();
    const base = baseOptions(theme);

    return {
      chartData: {
        labels: data.map((d) => d.label),
        datasets: [
          {
            label: "Attendance",
            data: data.map((d) => d.rate),
            borderColor: CHART_COLORS.accent,
            backgroundColor: "rgba(59,91,219,0.10)",
            borderWidth: 2,
            // Slight curve. Enough to read as a trend, not so much that it
            // implies values between months that were never measured.
            tension: 0.25,
            fill: true,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: data.map((d) =>
              d.rate >= THRESHOLD ? CHART_COLORS.present : CHART_COLORS.absent
            ),
            pointBorderColor: theme.surface,
            pointBorderWidth: 2,
            spanGaps: false, // break the line across months with no classes
          },
        ],
      },
      options: {
        ...base,
        plugins: {
          ...base.plugins,
          tooltip: {
            ...base.plugins.tooltip,
            callbacks: {
              label: (item) => `${item.parsed.y}% attendance`,
              footer: (items) => {
                const p = data[items[0].dataIndex];
                return `${p.attended} of ${p.total} · ${p.sessions} classes`;
              },
            },
          },
        },
        scales: { ...base.scales, y: percentScale(theme) },
      },
    };
  }, [data, mode]);

  if (!data.length) {
    return <NoData message="A trend appears once classes have been held across at least one month." />;
  }
  return (
    <Line data={chartData} options={options} plugins={[thresholdLinePlugin(THRESHOLD)]} />
  );
}

/* ==================================================================== *
 * 3. STUDENT PERCENTAGE DISTRIBUTION  —  histogram
 *
 * QUESTION: how is the class distributed?
 *
 * WHY A HISTOGRAM AND NOT A BAR PER STUDENT
 * Forty bars with roll numbers underneath is unreadable and answers the wrong
 * question. The class average hides shape entirely: an average of 78% could be
 * everyone hovering near 78, or half the class at 95 and half at 60. Those
 * demand completely different responses.
 *
 * Ten-point buckets make the shape visible instantly. Bars left of the line
 * carry the absent colour, so the chart is read by meaning rather than by
 * consulting a legend.
 * ==================================================================== */

export function StudentPercentChart({ students = [] }) {
  const { theme: mode } = useTheme();

  const { chartData, options, counted } = useMemo(() => {
    const theme = readTheme();
    const base = baseOptions(theme);

    // Ten buckets of ten points. Students with no data (null percentage) are
    // excluded rather than binned at zero -- they have not been measured.
    const buckets = Array.from({ length: 10 }, (_, i) => ({
      label: `${i * 10}–${i * 10 + 10}`,
      lower: i * 10,
      names: [],
    }));

    let counted = 0;
    for (const s of students) {
      if (s.percentage === null || s.percentage === undefined) continue;
      counted += 1;
      const idx = Math.min(9, Math.floor(s.percentage / 10));
      buckets[idx].names.push(s.roll_number);
    }

    return {
      counted,
      chartData: {
        labels: buckets.map((b) => b.label),
        datasets: [
          {
            label: "Students",
            data: buckets.map((b) => b.names.length),
            backgroundColor: buckets.map((b) =>
              b.lower < THRESHOLD ? CHART_COLORS.absent : CHART_COLORS.present
            ),
            borderRadius: 3,
          },
        ],
      },
      options: {
        ...base,
        plugins: {
          ...base.plugins,
          tooltip: {
            ...base.plugins.tooltip,
            callbacks: {
              label: (item) => {
                const n = item.parsed.y;
                return `${n} ${n === 1 ? "student" : "students"}`;
              },
              // Naming the students turns the chart from a summary into
              // something actionable -- you can see WHO is in the low bucket.
              footer: (items) => {
                const names = buckets[items[0].dataIndex].names;
                if (!names.length) return "";
                return names.length <= 6
                  ? names.join(", ")
                  : `${names.slice(0, 6).join(", ")} +${names.length - 6} more`;
              },
            },
          },
        },
        scales: {
          x: {
            ...base.scales.x,
            title: {
              display: true,
              text: "Attendance %",
              font: { family: "Instrument Sans", size: 11 },
              color: theme.text,
            },
          },
          y: { ...base.scales.y, ticks: { ...base.scales.y.ticks, precision: 0 } },
        },
      },
    };
  }, [students, mode]);

  if (!counted) {
    return <NoData message="No student has a recorded percentage yet. This chart fills in once classes are held." />;
  }
  return <Bar data={chartData} options={options} />;
}

/* ==================================================================== *
 * 4. SUBJECT PERCENTAGE  —  horizontal bars
 *
 * QUESTION: which of my classes is struggling?
 *
 * WHY HORIZONTAL
 * Subject names are long ("Data Structures and Algorithms"). On a vertical
 * chart they either rotate 45 degrees and become hard to read, or truncate.
 * Horizontal bars give labels a full line of horizontal space.
 *
 * The rule generalises: when your category labels are words rather than dates
 * or numbers, turn the bar chart sideways.
 * ==================================================================== */

export function SubjectPercentChart({ subjects = [] }) {
  const { theme: mode } = useTheme();

  const { chartData, options } = useMemo(() => {
    const theme = readTheme();
    const base = baseOptions(theme);
    const withData = subjects.filter((s) => s.rate !== null && s.rate !== undefined);

    return {
      chartData: {
        labels: withData.map((s) => `${s.subject_code} ${s.section}`),
        datasets: [
          {
            label: "Class average",
            data: withData.map((s) => s.rate),
            backgroundColor: withData.map((s) =>
              s.rate >= THRESHOLD ? CHART_COLORS.present : CHART_COLORS.absent
            ),
            borderRadius: 3,
            barThickness: 22,
          },
        ],
      },
      options: {
        ...base,
        indexAxis: "y", // this one line makes the chart horizontal
        plugins: {
          ...base.plugins,
          tooltip: {
            ...base.plugins.tooltip,
            callbacks: {
              label: (item) => `${item.parsed.x}% class average`,
              footer: (items) => {
                const s = withData[items[0].dataIndex];
                return `${s.enrolled_count} students · ${s.below_threshold_count} below 75%`;
              },
            },
          },
        },
        scales: {
          x: { ...percentScale(theme), grid: { color: theme.grid } },
          y: { ...base.scales.y, grid: { display: false } },
        },
      },
    };
  }, [subjects, mode]);

  if (!subjects.some((s) => s.rate !== null)) {
    return <NoData message="Class averages appear once each subject has held at least one class." />;
  }
  return <Bar data={chartData} options={options} />;
}

/* ==================================================================== *
 * 5. TOP ABSENTEES  —  horizontal bars, ranked by classes missed
 *
 * QUESTION: who is not turning up?
 *
 * WHY COUNT AND NOT PERCENTAGE -- the distinction that justifies this chart
 * A student who attended 1 of 2 classes sits at 50%. A student who attended
 * 30 of 45 sits at 67%. The second LOOKS better but has missed fifteen classes
 * against the first's one.
 *
 * Percentage answers "who risks being barred from the exam" -- the defaulter
 * list already does that. Count answers "who is actually absent", which is a
 * pastoral question and a different list. Ranking this by percentage would
 * make it a duplicate.
 *
 * Bars are coloured by whether the student is ALSO below threshold, so both
 * facts are visible at once.
 * ==================================================================== */

export function TopAbsenteesChart({ absentees = [] }) {
  const { theme: mode } = useTheme();

  const { chartData, options } = useMemo(() => {
    const theme = readTheme();
    const base = baseOptions(theme);

    return {
      chartData: {
        labels: absentees.map((a) => a.roll_number),
        datasets: [
          {
            label: "Classes missed",
            data: absentees.map((a) => a.absent),
            backgroundColor: absentees.map((a) =>
              a.is_below_threshold ? CHART_COLORS.absent : CHART_COLORS.late
            ),
            borderRadius: 3,
            barThickness: 18,
          },
        ],
      },
      options: {
        ...base,
        indexAxis: "y",
        plugins: {
          ...base.plugins,
          tooltip: {
            ...base.plugins.tooltip,
            callbacks: {
              title: (items) => absentees[items[0].dataIndex].student_name,
              label: (item) => `${item.parsed.x} classes missed`,
              footer: (items) => {
                const a = absentees[items[0].dataIndex];
                const pct = a.percentage !== null ? `${a.percentage}%` : "—";
                return `${a.attended} of ${a.total_sessions} attended · ${pct}`;
              },
            },
          },
        },
        scales: {
          x: {
            ...base.scales.x,
            grid: { color: theme.grid },
            ticks: { ...base.scales.x.ticks, precision: 0 },
          },
          y: { ...base.scales.y, grid: { display: false } },
        },
      },
    };
  }, [absentees, mode]);

  if (!absentees.length) {
    return <NoData message="Nobody has missed a class yet." />;
  }
  return <Bar data={chartData} options={options} />;
}
