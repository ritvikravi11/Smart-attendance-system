# User Manual
## Register — Attendance Management System

**Version 1.0**

This manual is organised by role. Find your section and read only that.

---

## 1. Getting Started

### Signing in

Open **http://localhost:5173** and enter the email and password your college issued you.

You land on a different page depending on your role: students see their attendance, faculty see their classes.

**If sign-in fails:**

| Message | Meaning |
|---------|---------|
| "Incorrect email or password" | Either the email is unknown or the password is wrong. The system deliberately does not say which — that would let someone discover which addresses have accounts. |
| "Account locked… try again in N minutes" | Five wrong passwords in a row. Wait, or ask an administrator to unlock it. |
| "This account has been deactivated" | Contact your administrator. |
| "Cannot reach the server" | The backend is not running. |

### Switching to dark mode

The sun/moon button at the top right. Your choice is remembered across sessions.

### Signing out

**Log out** at the bottom of the sidebar. Note that this ends your ability to obtain new access — an already-issued access token remains valid for up to 30 minutes, which is why you should not leave a session open on a shared machine.

---

## 2. For Students

### Your dashboard

The page answers one question first: **are you above the line?**

The large percentage at the top is your **overall attendance**. The bar beneath it has a marker at **75%** — the threshold most colleges require to sit examinations.

| What you see | Meaning |
|--------------|---------|
| Green figure, "Above the line" | You are meeting the requirement |
| Red figure, "N% short" | You are below it |
| **"—" and "No classes held yet"** | No classes have taken place. This is **not** 0% — it means there is nothing to measure yet. |

### Reading the subject list

Subjects are ordered **worst first**, deliberately — the one that needs your attention is at the top, not buried alphabetically.

Each row shows the same bar and threshold marker, plus counts by status:

| Badge | Meaning |
|-------|---------|
| **Present** | You were recognised or marked present |
| **Late** | You arrived after the grace period. **Counts as attended.** |
| **Absent** | You were not marked present when the class closed |
| **Excused** | Authorised absence. **Removed from the calculation entirely** — it neither helps nor hurts you. |

### The recovery figure

Below the threshold, a row tells you exactly what to do:

> Attend the next **12** classes in a row to reach 75%.

That number is calculated, not estimated. It answers the question a percentage cannot.

### How your percentage is calculated

```
                    present + late
percentage = ────────────────────────────── × 100
              eligible classes − excused
```

**Eligible classes** are those that were held and closed. Excluded:

- Classes **cancelled** by your college — they never happened
- Classes held **before you enrolled** in that subject
- Classes still **in progress**

So if a subject held 20 classes but 2 were cancelled and 3 were before you joined, your denominator starts at 15 — not 20.

### If something looks wrong

Speak to the faculty member who teaches that subject. They can correct any record while the session is open, and an administrator can reopen a closed one. Every change is logged with who made it and when.

---

## 3. For Faculty

### My classes

Each card is one class you teach:

| Figure | Meaning |
|--------|---------|
| **Students** | Enrolled in this offering |
| **Classes** | Sessions you have held and closed |
| **Average** | The class as a whole, green above 75% |

A red band appears when students are below the threshold. Click it to see who.

**Take attendance** is the primary action on every card.

### Taking attendance

#### 1. Open the session

Click **Take attendance**, then **Open session**. This creates today's register.

> Only one session can be open per class at a time. If you get "a session is already open", you have one from earlier that was never closed — close it first.

#### 2. Start the camera

Click **Start camera** and allow access when the browser asks.

The screen splits: **camera on the left, register on the right.** Both are visible at once so you can aim the camera while watching who is still missing.

#### 3. What the boxes mean

| Box colour | Meaning |
|------------|---------|
| **Green** with a name | Recognised and marked present |
| **Faded green** | Recognised, already marked — normal, not an error |
| **Amber** with "check" | Recognised, but the match was uncertain. Confirm it yourself. |
| **Violet, "Unknown"** | A face the system does not recognise. **Not the same as absent** — this is someone not enrolled, or a visitor. No record is created. |
| **Grey dashed** | The face was rejected before matching — too small, blurry, or turned away. Ask them to come closer or face the camera. |

#### 4. The register

Every enrolled student appears as a tile. Tiles fill in and flash briefly as students are recognised.

| Tile | Meaning |
|------|---------|
| Green | Present |
| Amber | Late |
| Red | Absent |
| Grey | Not yet marked |
| **Small dot** | Marked by the camera rather than by hand |
| **Amber ring** | Needs your review |

The header shows **"12 / 40 marked"** so you always know how many remain.

#### 5. Marking by hand

**Tap any tile** to open the marking panel and choose Present, Late, Absent, or Excused.

Use this when a student's face is not enrolled, when recognition misses someone, or when the camera is unavailable. **Manual marking always works** — the camera assists you, it does not replace your judgement.

#### 6. Close the session

Click **Close session**. Everyone still unmarked is recorded **absent**, and you get a summary.

> Once closed, records are locked. Only an administrator can reopen a session.

### If the camera does not work

| Problem | Fix |
|---------|-----|
| Browser never asks permission | Check the address bar for a blocked camera icon |
| "Camera is in use by another app" | Close Zoom, Teams, or OBS |
| **"The camera needs a secure connection"** | You are not on `localhost`. See the note below. |
| No camera at all | Mark students by hand — everything else works normally |

> **⚠️ The camera only works at `localhost` or over HTTPS.** If you open the app at an address like `http://192.168.1.5`, the browser blocks camera access. This is a browser security rule, not a fault in the system.

### Reports and analytics

**Reports** lists students below the threshold, ranked worst first, each with the number of consecutive classes needed to recover. **Export CSV** downloads the list.

**Analytics** shows five charts:

| Chart | Question it answers |
|-------|---------------------|
| **Daily attendance** | Was today unusual? Bar height is how many were in the room; the split shows the mix. |
| **Monthly trend** | Is the semester improving or slipping? |
| **Student distribution** | Is everyone near the average, or is the class split? An average of 78% could be everyone near 78, or half at 95 and half at 60 — very different situations. |
| **Subject comparison** | Which of your classes is struggling most? |
| **Most classes missed** | Who is not turning up? **Ranked by classes missed, not percentage** — a student at 67% who missed 15 classes needs a different conversation from one at 50% who missed 1. |

Hovering a chart shows the underlying numbers. On the distribution chart, hovering a bar names the students in that range.

---

## 4. For Administrators

Administrators can do everything faculty can, for **every** class, plus:

### Reopening a closed session

If a session was closed by mistake, open it from the class list and choose **Reopen**.

- Automatically-generated absent records are removed and will regenerate on the next close
- **Records a faculty member marked by hand are preserved**
- The action is logged

### Current limitations

Student, faculty, and subject management are **not yet available in the web interface**. Use the interactive API documentation at **http://localhost:8000/docs** — click **Authorize**, paste your access token, and use the endpoints directly.

**Face enrolment is not yet implemented at all.** Until it is, every face will be reported as Unknown and attendance must be taken by hand.

---

## 5. Frequently Asked Questions

**Can a student mark themselves present?**
No. Only faculty and administrators can open sessions or mark attendance. Students have read-only access to their own record, enforced on the server.

**Can one student see another's attendance?**
No. A student requesting another student's record receives a permission error.

**Can a faculty member see another's classes?**
No. Access is checked per class, per request, against who actually teaches it.

**What happens if two students look alike?**
The system reports both the match confidence and, internally, the margin to the second-best candidate. When a match is uncertain it is flagged **needs review** rather than accepted silently. This is why faculty override exists, and why you should check amber-ringed tiles.

**Could someone hold up a photograph?**
In this version, yes — anti-spoofing is not implemented. The mitigation is that you are in the room watching. This is a documented limitation, not an oversight.

**What is stored about my face?**
A 512-number mathematical vector, not a photograph. Images captured during enrolment are deleted after the vector is computed, and frames from the camera during attendance are never saved. The vector cannot be turned back into a recognisable photograph and is useless outside this system.

**Can I withdraw consent?**
Yes. An administrator can delete your biometric data entirely; your attendance history is unaffected, and you will be marked by hand thereafter.

**Why is my percentage different from what I counted?**
Cancelled classes, classes before you enrolled, and excused absences are all excluded. See §2.

---

## 6. Getting Help

| Problem | Who to ask |
|---------|-----------|
| Wrong attendance record | The faculty member for that subject |
| Cannot sign in | Your department administrator |
| Camera not working | See §3, then your administrator |
| Percentage looks wrong | Read §2, then ask your faculty member |

---

*End of User Manual v1.0*
