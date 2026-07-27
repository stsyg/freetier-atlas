import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import AdminApp from "./admin/AdminApp.tsx";
import "./index.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root was not found in the document.");
}

// The private admin surface lives under /admin (served by the SPA fallback and
// reachable at /api/admin on the API through the nginx proxy). Everything else
// renders the public catalogue/adviser app. A single path check keeps the app
// dependency-free (no router library).
const isAdmin = window.location.pathname.replace(/\/+$/, "") === "/admin";

createRoot(rootElement).render(
  <StrictMode>{isAdmin ? <AdminApp /> : <App />}</StrictMode>,
);
