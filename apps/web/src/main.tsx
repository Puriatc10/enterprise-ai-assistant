import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted variable font (Arabic + Latin subsets): no runtime network call,
// and the Persian glyphs are the same ones the rest of the UI assumes.
import "@fontsource-variable/vazirmatn";

// Stylesheet order is the cascade contract: tokens + base first, then the
// screens that consume them. Imported before `./App` on purpose — App.tsx also
// imports chat.css, and a component sheet must never load before the token
// layer that gives it colour.
import "./styles.css";
import "./auth/auth.css";
import "./features/chat/chat.css";

import { App } from "./App";

const container = document.getElementById("root");
if (!container) {
  throw new Error("index.html is missing the #root container.");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
