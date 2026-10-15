# Module 4 — Frontend

**Status:** Complete — builds clean, zero warnings
**Stack:** React 18 · Vite 5 · Tailwind 3 · React Router 6 · Recharts
**Bundle:** 257 kB initial (83 kB gzipped) + 365 kB lazy-loaded for Reports

---

## 1. Design Direction: "Register"

Before writing components I pinned the subject: **a college attendance register**. Its world is roll numbers, ruled grids, and one charged number — **75%**, the line that decides whether a student can sit the exam. That is not a statistic to a student; it is the whole product.

Two decisions followed:

**The signature element is the register grid.** A dense grid of roll-number tiles that fill in live. It mirrors both the paper register and the seating plan, and it is the one place the app looks like nothing else. Everything around it stays quiet — that's where the boldness is spent.

**Numbers are the content, so they get a real typeface.** Roll numbers, percentages, confidences and timestamps are set in IBM Plex Mono with `tabular-nums`. In a roster updating twice a second, proportional digits visibly jitter and columns fail to align. One CSS line fixes it.

### Palette

| Token | Hex | Meaning |
|-------|-----|---------|
| `ink-950…100` | `#0D1017` → `#E3E6EC` | Chrome, blue-cast so it doesn't read as pure grey |
| `accent` | `#3B5BDB` | Interactive only — focus, primary actions |
| `present` | `#12795C` | |
| `late` | `#B0761B` | |
| `absent` | `#A83E3E` | |
| **`unknown`** | **`#7C3FA8`** | **A stranger, not an absent student** |

**The semantic choice worth defending:** unknown is violet, not red. An unrecognised face is a *person in the room the system doesn't know*. An absent student is *someone who didn't come*. Different events, different responses, so different colours. Most systems collapse these and lose the distinction.

### Type

- **Bricolage Grotesque** — display, used with restraint: page titles and big numbers only
- **Instrument Sans** — body and UI
- **IBM Plex Mono** — all data

---

## 2. Component Inventory

### Primitives (`components/ui/primitives.jsx`)

| Component | What it does | Decision worth noting |
|-----------|--------------|----------------------|
| `Button` | Action trigger | Variants encode **intent** (`danger`), not colour. Auto-disables while loading — cheapest defence against double submission. |
| `Input` | Labelled field | Label + field + error are one component so the `htmlFor`/`aria-describedby` wiring can't be forgotten. |
| `Card` / `CardHeader` | Surface | Single surface primitive, so shadows and radii can't drift apart. |
| `StatusBadge` | Attendance status | **The app's shared vocabulary.** The one primitive that knows domain meaning, deliberately — so "present" looks identical in the roster, the reports table, and the camera overlay. |
| `Spinner` / `Skeleton` | Loading | Two affordances: spinner for actions, skeleton for content. Skeletons preserve layout so the page doesn't jump. |
| `EmptyState` | Nothing here | Every empty state names what's missing *and* offers the action that fills it. |
| `Alert` | Feedback | Takes a description, not just a title. A red box saying "Error" helps nobody. |
| `Stat` | Labelled number | Display face + tabular figures. |

### Signature components

**`RegisterGrid`** — the grid. Each tile encodes four things at once: fill colour = status, a dot = marked by camera, a ring = needs review, a flash = just recognised.

The flash is the only animation in the app that carries information. It answers the question a lecturer actually has while pointing a camera at a queue: *did that one register?*

The `useRecentlyMarked` hook is what makes it correct. The roster refreshes twice a second, so it diffs current state against previous state to detect a genuine **transition**. Without it, every tile would re-animate on every poll — signalling "just recognised" for someone marked five minutes ago.

**`ThresholdMeter`** — the student-facing signature. The 75% line is drawn *on* every bar, so the comparison is instant across six subjects. Below the line it shows the recovery count from the backend: "attend the next 12 classes in a row."

When `percentage` is null it renders an em-dash and says "No classes held yet" — never 0%, which would tell every student on day one that they're failing.

**`CameraStage`** — live video with the recognition overlay.

The whole difficulty is coordinates. The backend returns boxes in the frame's space (1280×720); the video element might be 640×360 or 900×506. The fix is rendering the overlay in **percentages** of the frame's intrinsic size. Percentages survive any display size, so boxes stay correct on resize, on a projector, and on a phone — with no resize listener.

The preview is mirrored (people expect a mirror of themselves), the labels are un-mirrored back so text reads, and the frame *sent to the server* is unmirrored so recognition is unaffected.

---

## 3. The Five Screens

### Login
A split screen, not a centred card on a gradient. The left panel renders the register motif as a static pattern — someone who's never seen the app learns what it does before typing anything.

Error handling is deliberately specific: a locked account (423) gets its own message because waiting will help, but a wrong password and an unknown email get the **same** message, because the backend deliberately doesn't distinguish them. The UI must not undermine that enumeration defence.

### Student Dashboard
One job: answer *"am I safe?"* before scrolling. So the hero is the overall threshold meter, not a row of stat cards.

**Subjects sort worst-first.** Alphabetical order buries the one row that matters.

### Faculty Dashboard
One job: get from "I'm about to teach" to "attendance is running" in one click. Every offering card is built around its primary action.

Two numbers per class — average and defaulter count — rather than a trend chart. A trend line looks like analytics and answers nothing a lecturer acts on. The defaulter count is a link, because it's the number you click.

Warnings appear **only when something is wrong**. A permanent green "all systems normal" banner is noise people learn to ignore.

### Attendance Screen
**Camera left, register right.** A lecturer needs both at once: the camera to aim, the register to see who's missing. Behind a tab would mean toggling during the one moment the room is filling.

**The capture loop is self-scheduling, not `setInterval`.** If the server takes 800 ms and the interval is 500 ms, requests pile up faster than they complete until the tab dies. Instead: capture → await → wait the *remaining* time → repeat. The rate adapts to whatever the server sustains.

The roster only re-fetches when `newly_marked > 0`. At 2 fps an unconditional refetch would be 120 pointless queries a minute.

### Reports Screen
A **histogram, not a trend line.** The useful question is "how is my class distributed?" — is everyone near 80%, or is it bimodal with a group who never come? The 75% reference line turns the chart into the same comparison the app is built around. Bars below the line carry the absent colour, so the chart is read by meaning rather than by legend.

CSV export is client-side. The data is already in the browser; a round trip to build a file the client can assemble would be slower for no benefit.

---

## 4. Two Bugs the Build Caught

### A Cyrillic С in an import
```
import { CircleСheckBig, ... } from "lucide-react";
                ↑ U+0421 CYRILLIC CAPITAL ES
```
Visually identical to Latin C. It would have resolved to `undefined` at runtime and rendered nothing — no error, no crash, just a missing icon. Vite's static analysis caught it; a runtime-only check would not have.

### A 620 kB bundle
Recharts is ~365 kB and only the Reports page uses it — so the **login page was downloading a charting library it never renders**. `lazy()` + `Suspense` defers it.

| | Before | After |
|---|--------|-------|
| Initial bundle | 620 kB | **257 kB** (83 kB gzipped) |

---

## 5. Quality Floor

Built in, not bolted on:

- **Responsive** to mobile — sidebar becomes a drawer, camera stacks above the register
- **Dark mode** — 43 dark rules compiled; the class is set by an inline script in `index.html` *before* React mounts, so there's no white flash on load
- **Visible keyboard focus** — `:focus-visible` rings, so mouse users don't see them
- **Reduced motion** respected — the tile flash is meaningful but nobody's forced to watch it
- **Colour is never the only channel** — every status has a text label; the register legend states each colour in words
- **ARIA** — `role="progressbar"` with values on meters, `aria-invalid`/`aria-describedby` on fields, labelled icon-only buttons

---

## 6. How to Run It

```
cd frontend
npm install
npm run dev
```

Opens `http://localhost:5173`. The Vite proxy forwards `/api` to `http://localhost:8000`, so **run the backend first**:

```
cd backend && uvicorn app.main:app --reload
```

Sign in with the seeded accounts:

| Role | Email | Password |
|------|-------|----------|
| Faculty | `das@college.edu` | `Faculty@123` |
| Student | `ravi@college.edu` | `Student@123` |
| Admin | `admin@college.edu` | `Admin@12345` |

### ⚠️ The demo-day trap, again

`getUserMedia` **only works on `localhost` or HTTPS**. Open the app at `http://192.168.1.x` from your phone and the camera silently dies.

The hook detects this and shows a specific message rather than a generic permissions error — but the real fix is planning your demo topology now. Demo on `localhost`, or tunnel with ngrok for HTTPS. This is risk R-2 from the design document, and it has ended more live demos than anything else on that list.

---

## 7. Definition of Done

- [ ] `npm install` completes
- [ ] `npm run build` succeeds with no warnings
- [ ] Backend running on `:8000`
- [ ] Login works for all three roles, each landing on its own home
- [ ] Dark mode toggles and persists across reload with no white flash
- [ ] Sidebar collapses to a drawer below `lg`
- [ ] Faculty can open a session and see the full register
- [ ] Tapping a tile marks a student manually
- [ ] Camera starts on `localhost` and draws boxes on faces
- [ ] Student sees the threshold meter and worst-subject-first ordering
- [ ] Committed: `feat(frontend): five screens with register grid UI [11.1-14.3]`

---

## 8. What's Missing

Honest gaps, in priority order:

1. **Face enrolment UI** — the biggest one. `POST /students/{id}/faces` still has no endpoint on the backend either, so the embedding index is empty and every face returns UNKNOWN. Until this exists, automatic marking cannot be demonstrated.
2. **Admin screens** — student and subject CRUD. Currently only reachable through `/docs`.
3. **PDF export** — CSV works; PDF is Tier 2.
4. **Toast notifications** — actions currently confirm through inline state changes only.

**Module 5 should be face enrolment, end to end** — backend endpoint plus capture UI. That closes the loop: enrol a face, open a session, point the camera, watch the register fill itself. That's the demo, and it's the one thing standing between you and a working system.
