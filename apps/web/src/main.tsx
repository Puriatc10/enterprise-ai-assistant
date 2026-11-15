import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
// Global sheet first so the auth screen can rely on its design tokens.
import "./styles.css";
import "./auth/auth.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("index.html is missing the #root container.");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
