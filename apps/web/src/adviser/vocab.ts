/**
 * Structured-form vocabularies for the deterministic adviser (F006 slice 4).
 *
 * The category list mirrors the backend's fourteen canonical categories
 * (`apps/api/app/read_api/taxonomy.py`) EXACTLY, so a category the form submits
 * is always one the API's strict schema accepts. The metric / unit / period
 * lists are ADVISORY suggestions surfaced through native `<datalist>`s only —
 * they never restrict the free-form structured input and the UI never derives
 * cost, Z0, or quota behaviour from them. This is a plain structured form: there
 * is no natural-language parsing here (that is F007).
 */

export interface Option {
  value: string;
  label: string;
}

/**
 * The fourteen canonical categories, in taxonomy order. Slugs must match
 * `app.read_api.taxonomy.CATEGORY_TAXONOMY`; the names mirror the display names
 * the read API returns so the form and the catalogue speak the same language.
 */
export const CATEGORY_OPTIONS: Option[] = [
  { value: "compute-vms", label: "Compute and virtual machines" },
  { value: "containers-app-hosting", label: "Containers and application hosting" },
  { value: "serverless-functions", label: "Serverless functions" },
  { value: "relational-databases", label: "Relational databases" },
  { value: "nosql-key-value", label: "NoSQL and key-value databases" },
  { value: "object-file-storage", label: "Object and file storage" },
  { value: "networking-cdn-dns", label: "Networking, CDN, and DNS" },
  { value: "queues-messaging-jobs", label: "Queues, messaging, and scheduled jobs" },
  { value: "auth-identity", label: "Authentication and identity" },
  { value: "cicd-source-control", label: "CI/CD and source control" },
  { value: "monitoring-logs-tracing", label: "Monitoring, logs, and tracing" },
  { value: "ai-inference-embeddings", label: "AI models, inference, and embeddings" },
  { value: "email-notifications-comms", label: "Email, notifications, and communications" },
  { value: "secrets-config-devtools", label: "Secrets, configuration, and developer tools" },
];

/** Advisory metric suggestions for the demand rows (datalist only). */
export const METRIC_SUGGESTIONS: string[] = [
  "storage",
  "requests",
  "bandwidth",
  "egress",
  "compute",
  "build_minutes",
  "invocations",
  "rows",
  "reads",
  "writes",
  "seats",
  "messages",
];

/** Advisory unit suggestions for the demand rows (datalist only). */
export const UNIT_SUGGESTIONS: string[] = [
  "GB",
  "MB",
  "TB",
  "requests",
  "count",
  "minutes",
  "hours",
  "vCPU",
  "seats",
  "messages",
];

/** Advisory period suggestions for the demand rows (datalist only). */
export const PERIOD_SUGGESTIONS: string[] = ["month", "day", "hour", "minute", "second"];
