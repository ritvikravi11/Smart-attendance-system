/**
 * Route guards.
 *
 * ProtectedRoute -- requires a signed-in user.
 * RoleRoute      -- additionally requires one of a set of roles.
 *
 * *** THESE ARE NOT SECURITY. ***
 * A route guard stops a person from navigating somewhere useless. It does not
 * stop anyone from calling the API directly with curl. The real enforcement is
 * the `require_role` dependency on the FastAPI side, which checks the JWT on
 * every single request.
 *
 * These guards exist so a student who types /attendance sees a sensible
 * redirect instead of a screen full of 403 errors. Usability, not defence.
 */

import { Navigate, Outlet, useLocation } from "react-router-dom";

import { Spinner } from "../components/ui/primitives";
import { useAuth } from "../context/AuthContext";

function FullPageSpinner() {
  return (
    <div className="flex h-full items-center justify-center">
      <Spinner className="h-6 w-6" />
    </div>
  );
}

export function ProtectedRoute() {
  const { isAuthenticated, loading } = useAuth();
  const location = useLocation();

  // The loading state matters: on a page refresh we hold a token but have not
  // yet confirmed who it belongs to. Redirecting during that window would bounce
  // a signed-in user to the login screen on every refresh.
  if (loading) return <FullPageSpinner />;

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}

export function RoleRoute({ allow = [] }) {
  const { role, loading } = useAuth();
  if (loading) return <FullPageSpinner />;
  if (!allow.includes(role)) {
    const home = role === "student" ? "/me" : "/faculty";
    return <Navigate to={home} replace />;
  }
  return <Outlet />;
}
