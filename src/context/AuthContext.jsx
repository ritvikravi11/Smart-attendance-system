/**
 * AuthContext -- who is logged in, app-wide.
 *
 * WHY CONTEXT RATHER THAN PROPS
 * -----------------------------
 * The current user is needed by the sidebar, the topbar, every route guard,
 * and most pages. Threading it through props would mean passing `user` through
 * components that do not use it just to reach ones that do -- "prop drilling".
 * Context lets any component read it directly.
 *
 * WHY NOT REDUX
 * -------------
 * Redux solves complex shared state with many writers. This is one object with
 * three writers (login, logout, restore). Context is the right size for the
 * problem; adding Redux here would be architecture for its own sake.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { errorMessage, setSessionExpiredHandler, tokenStore } from "../api/client";
import { authApi } from "../api/endpoints";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);

  // THREE states, not two. `loading` exists because on a page refresh we hold
  // a token but not yet a user -- we must ask the server who it belongs to.
  // Without this state the app would flash the login page for a moment before
  // the answer arrives, which looks broken.
  const [loading, setLoading] = useState(true);

  const logout = useCallback(async () => {
    const refresh = tokenStore.refresh;
    // Best-effort server-side revocation. If it fails (offline, expired), we
    // still clear locally -- the user asked to log out, so log them out.
    if (refresh) {
      try {
        await authApi.logout(refresh);
      } catch {
        /* ignore */
      }
    }
    tokenStore.clear();
    setUser(null);
  }, []);

  // Let the axios interceptor tell us when refresh has finally failed, so the
  // UI can drop to the login screen instead of showing an authenticated shell
  // full of failing requests.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      tokenStore.clear();
      setUser(null);
    });
  }, []);

  // Session restore on mount.
  useEffect(() => {
    let cancelled = false;
    async function restore() {
      if (!tokenStore.access) {
        setLoading(false);
        return;
      }
      try {
        const me = await authApi.me();
        if (!cancelled) setUser(me);
      } catch {
        tokenStore.clear();
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email, password) => {
    const tokens = await authApi.login(email, password);
    tokenStore.set(tokens);
    const me = await authApi.me();
    setUser(me);
    return me;
  }, []);

  // useMemo so the context value is not a fresh object every render -- without
  // it, every consumer re-renders whenever this provider does.
  const value = useMemo(
    () => ({
      user,
      loading,
      login,
      logout,
      isAuthenticated: Boolean(user),
      role: user?.role?.name ?? null,
      errorMessage,
    }),
    [user, loading, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  // A clear error beats "cannot read property of null" three frames deep.
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
