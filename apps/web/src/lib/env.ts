/**
 * Build-time configuration, read once and validated once.
 *
 * Vite inlines `import.meta.env.VITE_*` at build time, so these values are
 * baked into the bundle. That is why only public values live here: the
 * Supabase anon key is designed to be public (row-level security protects the
 * data), while a service_role key must never appear in this repository.
 *
 * Nothing in this module throws. A missing variable has to become a visible
 * Persian notice in the UI, not a blank page caused by a module-level throw.
 */

const DEFAULT_API_BASE_URL = "http://localhost:8000";

/** Environment variable names, kept in one place so messages stay accurate. */
export const ENV_KEYS = {
  supabaseUrl: "VITE_SUPABASE_URL",
  supabaseAnonKey: "VITE_SUPABASE_ANON_KEY",
  apiBaseUrl: "VITE_API_BASE_URL",
} as const;

export interface AppEnv {
  /** Supabase project URL, or null when unset/blank. */
  supabaseUrl: string | null;
  /** Public anon key, or null when unset/blank. */
  supabaseAnonKey: string | null;
  /** Backend base URL, always absolute and without a trailing slash. */
  apiBaseUrl: string;
  /** Names of the variables that must be provided for auth to work. */
  missing: string[];
}

/**
 * Read one variable defensively. Vite's type for unknown keys is loose, so the
 * value is narrowed with `unknown` instead of trusted. A blank string counts as
 * missing: `.env` files copied from a template are often full of empty values.
 */
function readVar(key: string): string {
  const source = import.meta.env as unknown as Record<string, unknown>;
  const value = source[key];
  return typeof value === "string" ? value.trim() : "";
}

function normalizeBaseUrl(raw: string): string {
  if (!raw) {
    return DEFAULT_API_BASE_URL;
  }
  // A base URL without a scheme ("localhost:8000") would make fetch treat the
  // API path as relative and fail confusingly. Fall back to the documented
  // local default instead of producing broken requests.
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return DEFAULT_API_BASE_URL;
    }
  } catch {
    return DEFAULT_API_BASE_URL;
  }
  return raw.replace(/\/+$/, "");
}

function buildEnv(): AppEnv {
  const supabaseUrl = readVar(ENV_KEYS.supabaseUrl) || null;
  const supabaseAnonKey = readVar(ENV_KEYS.supabaseAnonKey) || null;
  const apiBaseUrl = normalizeBaseUrl(readVar(ENV_KEYS.apiBaseUrl));

  const missing: string[] = [];
  if (!supabaseUrl) {
    missing.push(ENV_KEYS.supabaseUrl);
  }
  if (!supabaseAnonKey) {
    missing.push(ENV_KEYS.supabaseAnonKey);
  }

  return { supabaseUrl, supabaseAnonKey, apiBaseUrl, missing };
}

export const env: AppEnv = buildEnv();

/** True when Supabase auth can be created at all. */
export const isAuthConfigured: boolean =
  env.supabaseUrl !== null && env.supabaseAnonKey !== null;
