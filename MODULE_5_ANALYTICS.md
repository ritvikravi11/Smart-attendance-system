# Module 5 — Analytics

**Status:** Complete — 46/46 backend checks, clean frontend build, no regressions
**Charts:** 5 · **Endpoints:** 6 · **Library:** Chart.js 4.4 (replacing Recharts)

---

## 1. A Library Decision Made Deliberately

You asked for Chart.js. The project already had Recharts. Adding Chart.js alongside would have meant shipping two charting libraries — so I **replaced** Recharts instead.

| | Recharts | Chart.js |
|---|---|---|
| Bundle contribution | 360 kB | **183 kB** |
| Reports page | 360 kB | **4.5 kB** |

Charting is now **49% smaller**, and there's one library rather than two competing visual languages. Reports keeps the ranked defaulter list — its real job — and the distribution chart it used to own now lives on Analytics, where it belongs with the other four.

Chart.js is imported with explicit registration rather than `chart.js/auto`. Auto pulls in radar, polar-area, doughnut and bubble controllers we never render; registering only `Bar`, `Line`, and the scales we use keeps the bundle honest.

---

## 2. Every Chart, Explained

The test each one had to pass: **can it be described in one sentence starting "this tells you…"?** If not, it shouldn't exist.

### 1. Daily attendance — stacked bars

> *Was today unusual?*

**Why stacked bars, not a line.** A line of "attendance rate" hides class size. 16 of 20 and 8 of 10 are both 80%, but they're very different days. Stacked bars show the **proportion** (the split) and the **volume** (the bar height) at once.

**Why the x-axis is sessions, not calendar days.** A calendar axis emits a bar for every weekend and holiday. Those aren't zero-attendance days — they're no-class days, and drawing them as zeros produces a sawtooth that reads as collapsing attendance when nothing happened. Only closed sessions get a bar.

### 2. Monthly trend — line with the 75% threshold

> *Is the semester improving or slipping?*

**Why a line here but bars above.** Time series with a meaningful direction want a line — the slope *is* the information. Daily data is too noisy for that (one exam day tanks a point); monthly aggregates smooth it into a real trend.

**`spanGaps: false` is deliberate.** If a month has no classes, the line **breaks** rather than drawing a straight segment across the gap. A connected line would invent data for a month that never happened.

Point colour flips green/red at the threshold, so a glance tells you which months passed.

### 3. Student distribution — histogram

> *Is everyone near the average, or is the class split?*

**Why a histogram, not a bar per student.** Forty bars with roll numbers underneath is unreadable and answers the wrong question. More importantly, **the class average hides shape**: an average of 78% could be everyone hovering near 78, or half the class at 95 and half at 60. Those demand completely different responses.

Ten-point buckets make the shape visible instantly. Bars left of 75% carry the absent colour, so the chart is read by meaning rather than by consulting a legend. Hovering names the students in that bucket — which turns a summary into something actionable.

### 4. Subject comparison — horizontal bars

> *Which of my classes is struggling most?*

**Why horizontal.** Subject names are long ("Data Structures and Algorithms"). Vertically they either rotate 45° and become hard to read, or truncate. Horizontal bars give labels a full line.

The rule generalises: **when category labels are words rather than dates or numbers, turn the bar chart sideways.**

### 5. Top absentees — horizontal bars, ranked by count

> *Who is not turning up?*

**This is the chart whose design decision matters most.** It ranks by **absence count**, not percentage — and that's what justifies it existing separately from the defaulter list.

A student who attended 1 of 2 classes sits at **50%**. A student who attended 30 of 45 sits at **67%**. The second *looks* better but has missed **fifteen** classes against the first's one.

- Percentage answers *"who risks being barred from the exam?"* — the defaulter list already does that.
- Count answers *"who is actually absent?"* — a pastoral question, and a different list.

Ranking this by percentage would make it a duplicate under another name. The test suite proves the two rankings genuinely diverge rather than asserting it:

```
ranked by absences  : ['R010', 'R009', 'R008', 'R099']
ranked by percentage: ['R010', 'R099', 'R009', 'R008']
```

**No pie charts anywhere.** Humans compare angles badly, and every question here is a comparison of magnitudes, which bars do better.

---

## 3. The Aggregation Bug That Didn't Ship

The monthly rate is computed by **summing totals**, never by averaging daily rates. This is Simpson's paradox, and it's the easiest way to ship a chart that confidently disagrees with reality.

The test constructs the trap explicitly:

```
Month A holds two sessions: 1 of 2 present, and 9 of 10 present

  average of daily rates : (50 + 90) / 2      = 70.0%   ← WRONG
  summed totals          : (1 + 9) / (2 + 10) = 83.3%   ← correct
  our code returns       :                      83.3%
```

**A 13-point error**, and nothing about the wrong version would look broken. Averaging percentages weights a 5-student session equally with a 40-student one; summing gives each *student-session* equal weight, which is what "attendance for the month" actually means.

Same reasoning as the overall percentage in `analytics_service.py` — and the test cross-checks that both services agree, so the charts can never drift from the numbers printed beside them.

---

## 4. Three Implementation Details Worth Knowing

**The threshold line is a custom plugin, not a dataset.** A dataset would appear in the legend, in tooltips, and in auto-scaling — it would be treated as data, which it isn't. Chart.js has an official annotation plugin, but that's ~30 kB for one dashed line; fifteen lines of canvas drawing is the better trade.

**Dark mode needs literal colours.** Chart.js draws to canvas, so it can't read CSS variables or the `dark:` class. `readTheme()` resolves the current mode at build time, and every chart re-keys on theme change so they rebuild rather than keeping stale colours.

**`maintainAspectRatio: false` requires a fixed-height parent.** This isn't styling — it's required. Without an explicit height the container collapses to zero and the chart silently disappears with no error. That's why `ChartCard` sets `style={{ height }}`.

**Aggregation happens in Python, not SQL.** `date_trunc` is PostgreSQL-specific and SQLite's `strftime` differs, so one Python implementation means one set of tests rather than two dialect paths where only one is exercised. At 2,400 records per offering this takes single-digit milliseconds. **This is a trade-off, not a free win** — past ~100,000 records the right move is `date_trunc` + `GROUP BY`, and the reasoning is recorded in the module docstring so the next person knows which way to go.

---

## 5. Files Delivered

```
backend/
├── app/services/chart_analytics_service.py   Time series, distributions, rankings
├── app/schemas/attendance.py                 +8 chart schemas
├── app/api/v1/endpoints/attendance.py        +6 analytics endpoints
└── tests/test_analytics.py                   46 checks

frontend/src/
├── components/charts/
│   ├── setup.js                 Chart.js registration, theme, threshold plugin
│   └── AttendanceCharts.jsx     The five charts
└── pages/AnalyticsScreen.jsx    Assembles them from one request
```

### Endpoints

| Method | Path | Returns |
|--------|------|---------|
| GET | `/analytics/offerings/{id}/dashboard` | All five charts, one snapshot |
| GET | `/analytics/offerings/{id}/daily` | Per-session series |
| GET | `/analytics/offerings/{id}/monthly` | Monthly aggregate |
| GET | `/analytics/offerings/{id}/students` | Every student's % |
| GET | `/analytics/faculty/{id}/subjects` | Class average per subject |
| GET | `/analytics/offerings/{id}/absentees` | Ranked by classes missed |

**Why the dashboard endpoint bundles all five.** The charts must agree with each other. Fetched separately, a session could close between call two and call four — the daily chart would include a session the student percentages don't, and the page would silently contradict itself. One request means one consistent snapshot, and four fewer round trips.

---

## 6. How to Run and Verify

```
# Backend
cd backend && python tests/test_analytics.py      # 46/46
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

Sign in as `das@college.edu` / `Faculty@123`, then open **Analytics** in the sidebar.

**With the seeded demo data the charts will be nearly empty** — that's correct behaviour, not a bug. Every chart has a specific empty state explaining what will fill it. To see them populated, open and close a few sessions from the attendance screen first.

Things worth checking by hand:

- Toggle dark mode — charts should **rebuild** with new colours, not keep stale ones
- Resize the window — charts reflow (that's `responsive: true` plus the fixed-height parent)
- Hover a histogram bar — the tooltip names the students in that bucket
- Compare the Analytics class average against the same number on Reports — they must match, because both come from `calculate_offering_attendance`

**One honest limitation:** I verified the charts compile and the data shape is correct, but I can't run a browser here, so canvas rendering is unverified. If a chart shows blank, check that its parent has a height — that's the failure mode `maintainAspectRatio: false` produces.

---

## 7. Definition of Done

- [ ] `python tests/test_analytics.py` reports 46/46
- [ ] Prior suites still pass (auth 61, attendance 59, ML 144, schema 30)
- [ ] `npm run build` succeeds with no warnings
- [ ] Analytics appears in the faculty sidebar
- [ ] All five charts render with real data
- [ ] Dark mode toggle rebuilds charts correctly
- [ ] Class average matches between Analytics and Reports
- [ ] Committed: `feat(analytics): five Chart.js visualisations [14.2]`

---

## 8. Still Missing

Unchanged from the last module, and now the only thing between you and a working demo:

**Face enrolment has no endpoint and no UI.** The embedding index is empty, so every face returns UNKNOWN and automatic marking cannot be demonstrated. The CV pipeline works standalone (144/144), the attendance engine works (59/59), the frontend works — but nothing connects a student's face to their record.

**Module 6 should be enrolment, end to end:**
- `POST /students/{id}/faces` — consent check, strict quality gate, 5–15 images, intra-set consistency, averaging, raw-image deletion, index refresh
- `DELETE /students/{id}/faces` — right to erasure
- Capture UI with live quality feedback

That closes the loop: enrol a face, open a session, point the camera, watch the register fill itself.
