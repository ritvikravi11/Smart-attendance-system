/**
 * CHART.JS SETUP
 * ==============
 *
 * WHY REGISTRATION IS EXPLICIT
 * ----------------------------
 * Chart.js v4 is tree-shakeable: nothing is bundled until you register it.
 * Importing the whole library via `chart.js/auto` would pull in every chart
 * type, scale and plugin -- including radar, polar area, and bubble charts we
 * never render. Registering only what we use keeps the bundle honest.
 *
 * WHY A SHARED THEME MODULE
 * -------------------------
 * Five charts across two pages. If each configured its own fonts, grid colours
 * and tooltip styling, they would drift apart, and the charts would look like
 * they came from different applications. This file is the single source.
 *
 * DARK MODE IS THE HARD PART. Chart.js draws to a canvas, so it cannot use CSS
 * variables or the `dark:` class -- it needs literal colour values at
 * configuration time. `readTheme()` resolves the current mode when a chart is
 * built, and the components re-key on theme change so charts rebuild rather
 * than keeping stale colours.
 */

import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Filler,
  Legend,
  LineController,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from "chart.js";

Chart.register(
  BarController,
  BarElement,
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Filler
);

/* Design tokens, duplicated as literals because canvas cannot read CSS vars.
   Kept in ONE place so the duplication is managed rather than scattered. */
export const CHART_COLORS = {
  present: "#12795C",
  late: "#B0761B",
  absent: "#A83E3E",
  excused: "#5B6478",
  unknown: "#7C3FA8",
  accent: "#3B5BDB",
  threshold: "#A83E3E",
};

export function readTheme() {
  const dark = document.documentElement.classList.contains("dark");
  return {
    dark,
    text: dark ? "#99A1B3" : "#5B6478",
    textStrong: dark ? "#E3E6EC" : "#1E2534",
    grid: dark ? "rgba(255,255,255,0.06)" : "rgba(13,16,23,0.06)",
    surface: dark ? "#1E2534" : "#FFFFFF",
    border: dark ? "rgba(255,255,255,0.10)" : "rgba(13,16,23,0.10)",
  };
}

/**
 * Options every chart shares.
 *
 * `maintainAspectRatio: false` is essential: with it true, Chart.js sizes the
 * canvas from its own ratio and ignores the container height, so charts
 * overflow their cards. Setting it false makes the parent's height authoritative
 * -- which is why every chart in this app is wrapped in a fixed-height div.
 */
export function baseOptions(theme) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      // "index" means hovering anywhere in a column highlights every dataset
      // at that x-position. For stacked attendance bars that is what people
      // expect -- they want the whole day's numbers, not one segment.
      mode: "index",
      intersect: false,
    },
    plugins: {
      legend: {
        display: false, // enabled per-chart only where it earns its space
        labels: {
          usePointStyle: true,
          boxWidth: 8,
          font: { family: "Instrument Sans", size: 12 },
          color: theme.text,
        },
      },
      tooltip: {
        backgroundColor: theme.dark ? "#0D1017" : "#1E2534",
        titleFont: { family: "Instrument Sans", size: 12, weight: "600" },
        // Mono for the numbers, so tooltip values align and do not jitter.
        bodyFont: { family: "IBM Plex Mono", size: 12 },
        padding: 10,
        cornerRadius: 6,
        displayColors: true,
        boxPadding: 4,
      },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: {
          font: { family: "IBM Plex Mono", size: 11 },
          color: theme.text,
          maxRotation: 0,
          autoSkipPadding: 12,
        },
      },
      y: {
        grid: { color: theme.grid },
        border: { display: false },
        ticks: {
          font: { family: "IBM Plex Mono", size: 11 },
          color: theme.text,
          padding: 6,
        },
      },
    },
  };
}

/**
 * A dashed horizontal line at the pass threshold.
 *
 * WHY A CUSTOM PLUGIN RATHER THAN AN EXTRA DATASET
 * ------------------------------------------------
 * A dataset would appear in the legend, in tooltips, and in the auto-scaling
 * calculation -- it would be treated as data, which it is not. It is an
 * annotation.
 *
 * Chart.js has an official annotation plugin, but it is a separate ~30kB
 * dependency for one dashed line. Fifteen lines of canvas drawing is the
 * better trade here.
 *
 * The line matters because it turns every chart into the same comparison the
 * whole product is built around: above the line or below it.
 */
export function thresholdLinePlugin(value = 75, label = "75% required") {
  return {
    id: "thresholdLine",
    afterDatasetsDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      const y = scales.y?.getPixelForValue(value);
      // Bail out if the threshold is off-scale, or we would draw outside the
      // plot area and over the axis labels.
      if (y == null || y < chartArea.top || y > chartArea.bottom) return;

      ctx.save();
      ctx.beginPath();
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = CHART_COLORS.threshold;
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();

      ctx.setLineDash([]);
      ctx.font = "500 10px 'IBM Plex Mono'";
      ctx.fillStyle = CHART_COLORS.threshold;
      ctx.textAlign = "right";
      ctx.textBaseline = "bottom";
      ctx.fillText(label, chartArea.right - 2, y - 3);
      ctx.restore();
    },
  };
}

/** Percentage y-axis, capped at 100 with a fixed 25-point step. */
export function percentScale(theme) {
  return {
    ...baseOptions(theme).scales.y,
    min: 0,
    max: 100,
    ticks: {
      ...baseOptions(theme).scales.y.ticks,
      stepSize: 25,
      callback: (v) => `${v}%`,
    },
  };
}
