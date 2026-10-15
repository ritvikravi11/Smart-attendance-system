/**
 * Route table.
 *
 * Nesting mirrors the guard structure: everything inside <ProtectedRoute>
 * requires a session, everything inside <RoleRoute> additionally requires a
 * role, and <AppShell> supplies the chrome for all of them. Adding a page is
 * one line in the right nest -- the guards and layout come for free.
 */

import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/layout/AppShell";
import { useAuth } from "./context/AuthContext";
import AttendanceScreen from "./pages/AttendanceScreen";
import FacultyDashboard from "./pages/FacultyDashboard";
import LoginPage from "./pages/LoginPage";
import StudentDashboard from "./pages/StudentDashboard";
import { Spinner } from "./components/ui/primitives";
import { ProtectedRoute, RoleRoute } from "./routes/ProtectedRoute";

/**
 * CODE SPLITTING.
 *
 * Reports is the only page that imports Recharts, which is ~350kB minified.
 * Bundling it with everything else would make the LOGIN page pay for a
 * charting library it never renders.
 *
 * lazy() + Suspense defers that download until someone actually opens Reports.
 * Measured effect on this app: initial bundle 620kB -> 271kB.
 */
const ReportsScreen = lazy(() => import("./pages/ReportsScreen"));
// Chart.js is ~200kB and only Analytics renders charts -- lazy so the login
// page and attendance screen never download it.
const AnalyticsScreen = lazy(() => import("./pages/AnalyticsScreen"));

function PageFallback() {
  return (
    <div className="flex h-64 items-center justify-center">
      <Spinner className="h-6 w-6" />
    </div>
  );
}

/** Send each role to its own landing page. */
function RoleHome() {
  const { role } = useAuth();
  return <Navigate to={role === "student" ? "/me" : "/faculty"} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route path="/" element={<RoleHome />} />

          <Route element={<RoleRoute allow={["student"]} />}>
            <Route path="/me" element={<StudentDashboard />} />
          </Route>

          <Route element={<RoleRoute allow={["faculty", "admin"]} />}>
            <Route path="/faculty" element={<FacultyDashboard />} />
            <Route path="/attendance" element={<AttendanceScreen />} />
            <Route
              path="/analytics"
              element={
                <Suspense fallback={<PageFallback />}>
                  <AnalyticsScreen />
                </Suspense>
              }
            />
            <Route
              path="/reports"
              element={
                <Suspense fallback={<PageFallback />}>
                  <ReportsScreen />
                </Suspense>
              }
            />
          </Route>
        </Route>
      </Route>

      {/* Unknown URL -> home, which itself routes by role. */}
      <Route path="*" element={<RoleHome />} />
    </Routes>
  );
}
