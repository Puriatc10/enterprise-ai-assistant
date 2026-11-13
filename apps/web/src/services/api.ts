/**
 * API access layer. All HTTP calls live here so components never build URLs.
 *
 * The base URL comes from configuration (VITE_API_BASE_URL), with a development
 * default. No endpoint, credential or token is ever hardcoded in a component.
 */

import type { HealthResponse } from "../types/api";

const DEFAULT_API_BASE_URL = "http://localhost:8000";

export const API_BASE_URL: string = (
  import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL
).replace(/\/+$/, "");

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string, timeoutMs = 10_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new ApiError(`Request failed with status ${response.status}`, response.status);
    }

    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("The backend did not answer in time.", 0);
    }
    throw new ApiError(
      `Cannot reach the backend at ${API_BASE_URL}. Is the API running?`,
      0,
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Liveness/readiness of the backend. Requires no credentials. */
export function fetchHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>("/health");
}
