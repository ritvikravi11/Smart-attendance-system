/**
 * Design tokens.
 *
 * DESIGN DIRECTION: "Register"
 * ---------------------------
 * The subject is a college attendance register. Its world is roll numbers,
 * ruled grids, and one charged number: 75%. In Indian engineering colleges
 * that threshold decides whether you sit the exam, so it is not a statistic --
 * it is the emotional centre of the whole product for a student.
 *
 * Two decisions follow from that:
 *
 * 1. THE SIGNATURE ELEMENT is the register grid -- a dense grid of roll-number
 *    tiles that fill in live as students are recognised. It mirrors the
 *    physical register and the seating plan at once, and it is the one place
 *    where this app looks like nothing else. Everything around it stays quiet.
 *
 * 2. NUMBERS ARE THE CONTENT, so they get a real typeface. Roll numbers,
 *    percentages, confidences and timestamps are set in IBM Plex Mono with
 *    tabular figures, so columns align and digits never jitter as they update.
 *
 * COLOUR: ink navy chrome, paper content, and a signal system where each
 * status means exactly one thing.
 *
 * The important semantic choice: UNKNOWN IS NOT ABSENT. An unrecognised face
 * is a stranger in the room, not a student who failed to attend. They are
 * different events with different responses, so they get different colours --
 * violet for unknown, red for absent. Most systems collapse these and lose
 * the distinction.
 */

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // Chrome: deep blue-cast ink for navigation and dark mode surfaces
        ink: {
          950: "#0D1017",
          900: "#141926",
          800: "#1E2534",
          700: "#2C3548",
          600: "#3D4759",
          500: "#5B6478",
          400: "#7A8397",
          300: "#99A1B3",
          200: "#C3C9D4",
          100: "#E3E6EC",
        },
        paper: "#F4F5F8",

        // Interactive accent. Used for focus, primary actions, live state.
        accent: {
          DEFAULT: "#3B5BDB",
          hover: "#2F4BC4",
          soft: "#EEF1FD",
          ring: "#3B5BDB",
        },

        // SIGNAL SYSTEM -- one colour, one meaning, never reused decoratively
        present: { DEFAULT: "#12795C", soft: "#E4F3EE", ink: "#0B5741" },
        late: { DEFAULT: "#B0761B", soft: "#FBF1DF", ink: "#7E5412" },
        absent: { DEFAULT: "#A83E3E", soft: "#F8E9E9", ink: "#7C2C2C" },
        excused: { DEFAULT: "#5B6478", soft: "#EEF0F4", ink: "#3D4759" },
        // Violet: a face the system does not recognise. NOT the same as absent.
        unknown: { DEFAULT: "#7C3FA8", soft: "#F3EBF9", ink: "#5A2C7A" },
      },

      fontFamily: {
        // Display: used with restraint -- page titles and the big numbers only
        display: ["'Bricolage Grotesque'", "system-ui", "sans-serif"],
        // Body: everything else
        sans: ["'Instrument Sans'", "system-ui", "-apple-system", "sans-serif"],
        // Data: roll numbers, percentages, timestamps, confidence scores
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },

      fontSize: {
        // Deliberate scale rather than Tailwind's defaults, so headings relate
        // to each other by ratio instead of by accident.
        "display-lg": ["3.25rem", { lineHeight: "1.02", letterSpacing: "-0.03em", fontWeight: "700" }],
        "display": ["2.25rem", { lineHeight: "1.08", letterSpacing: "-0.025em", fontWeight: "700" }],
        "title": ["1.375rem", { lineHeight: "1.2", letterSpacing: "-0.015em", fontWeight: "600" }],
        "eyebrow": ["0.6875rem", { lineHeight: "1", letterSpacing: "0.12em", fontWeight: "600" }],
      },

      borderRadius: {
        // Softer than sharp, harder than pill. Consistent across the app so
        // nothing looks borrowed from another design system.
        card: "10px",
        tile: "6px",
      },

      boxShadow: {
        card: "0 1px 2px rgba(13,16,23,0.04), 0 4px 12px rgba(13,16,23,0.04)",
        lift: "0 2px 4px rgba(13,16,23,0.06), 0 12px 28px rgba(13,16,23,0.10)",
        // Used only on the register grid when a tile flips to present
        flash: "0 0 0 3px rgba(18,121,92,0.22)",
      },

      keyframes: {
        // The register tile "marking" moment -- the one animation that carries
        // meaning rather than decorating. A student was just recognised.
        mark: {
          "0%": { transform: "scale(1)", boxShadow: "0 0 0 0 rgba(18,121,92,0.5)" },
          "45%": { transform: "scale(1.06)", boxShadow: "0 0 0 6px rgba(18,121,92,0)" },
          "100%": { transform: "scale(1)", boxShadow: "0 0 0 0 rgba(18,121,92,0)" },
        },
        scan: {
          "0%": { transform: "translateY(0%)", opacity: "0" },
          "10%": { opacity: "0.5" },
          "90%": { opacity: "0.5" },
          "100%": { transform: "translateY(100%)", opacity: "0" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        mark: "mark 600ms cubic-bezier(0.16, 1, 0.3, 1)",
        scan: "scan 2.4s linear infinite",
        "fade-up": "fade-up 240ms ease-out both",
      },
    },
  },
  plugins: [],
};
