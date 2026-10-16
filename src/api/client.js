/**
 * HTTP client.
 *
 * WHY EVERY REQUEST GOES THROUGH THIS FILE
 * ----------------------------------------
 * If components called axios directly, changing an endpoint, adding a header,
 * or handling a 401 would mean hunting through the whole codebase. Funnelling
 * through one client means those changes touch one file.
 *
 * Two interceptors do the work no component should have to think about:
 *
 *   REQUEST  -- attach the access token to every call.
 *   RESPONSE -- on a 401, silently refresh the token and retry the original
 *               request. The user never sees the 30-minute expiry.
 *
 * The refresh flow has one subtlety worth understanding: if five requests fire
 * at once and all get a 401, a naive implementation starts five refreshes.
 * Four of them present an already-rotated token and fail, logging the user out
 * for no reason. The `refreshPromise` singleton below makes the first 401 do
 * the refresh and the other four wait on its result.
 */

import axios from "axios";

const ACCESS_KEY = "attendance.access";
const REFRESH_KEY = "attendance.refresh";

export const tokenStore = {
  get access() {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set({ access_token, refresh_token }) {
    if (access_token) localStorage.setItem(ACCESS_KEY, access_token);
    if (refresh_token) localStorage.setItem(REFRESH_KEY, refresh_token);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

// Vite's dev proxy forwards /api to :8000. In production, VITE_API_BASE_URL
// points to the separately deployed FastAPI service; keeping a relative
// fallback preserves same-origin deployments.
const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "/api/v1")
  .replace(/\/$/, "");

export const api = axios.create({
  baseURL: apiBaseUrl,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const token = tokenStore.access;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

let refreshPromise = null;
let onSessionExpired = () => {};

/** Let AuthContext register what happens when refresh finally fails. */
export function setSessionExpiredHandler(fn) {
  onSessionExpired = fn;
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    const status = error.response?.status;

    // Only attempt a refresh for a genuine 401, and only once per request.
    // `_retried` prevents an infinite loop if the refreshed token is also
    // rejected.
    const isAuthEndpoint = original?.url?.includes("/auth/");
    if (status !== 401 || original?._retried || isAuthEndpoint) {
      return Promise.reject(error);
    }

    const refreshToken = tokenStore.refresh;
    if (!refreshToken) {
      onSessionExpired();
      return Promise.reject(error);
    }

    original._retried = true;

    // Singleton: concurrent 401s share ONE refresh instead of racing.
    if (!refreshPromise) {
      refreshPromise = axios
.post(`${apiBaseUrl}/auth/refresh`, {
  refresh_token: refreshToken,
})        .then((res) => {
          tokenStore.set(res.data);
          return res.data.access_token;
        })
        .catch((err) => {
          tokenStore.clear();
          onSessionExpired();
          throw err;
        })
        .finally(() => {
          refreshPromise = null;
        });
    }

    try {
      const newToken = await refreshPromise;
      original.headers.Authorization = `Bearer ${newToken}`;
      return api(original);
    } catch {
      return Promise.reject(error);
    }
  }
);

/**
 * Turn any error into a message worth showing a person.
 *
 * The backend returns one consistent envelope: { detail, error_code, field }.
 * Because it is consistent, this function is short -- which is the payoff for
 * having standardised errors on the server.
 *
 * Note the network-failure branch. "Network Error" is meaningless to a user;
 * "Cannot reach the server" plus what to do about it is not.
 */
export function errorMessage(error) {
  if (error?.response?.data?.detail) return error.response.data.detail;
  if (error?.code === "ERR_NETWORK") {
    return "Cannot reach the server. Check that the backend is running.";
  }
  return "Something went wrong. Try again.";
}

export function errorCode(error) {
  return error?.response?.data?.error_code ?? null;
}
