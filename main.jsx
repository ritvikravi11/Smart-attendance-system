import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { AuthProvider } from "./context/AuthContext";
import { ThemeProvider } from "./context/ThemeContext";
import "./index.css";

/**
 * Provider order matters: Router must wrap AuthProvider, because AuthProvider's
 * consumers call useNavigate. ThemeProvider is independent and can sit anywhere.
 */
createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        {/* Outer boundary: last resort. Catches failures in the providers
            themselves, which would otherwise blank the app before any inner
            boundary mounts. */}
        <ErrorBoundary>
          <AuthProvider>
            <App />
          </AuthProvider>
        </ErrorBoundary>
      </ThemeProvider>
    </BrowserRouter>
  </StrictMode>
);
