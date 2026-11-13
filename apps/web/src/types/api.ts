/**
 * Shapes returned by the backend.
 *
 * Kept in sync with the FastAPI response models (`app/api/routes/health.py`).
 * Phase 5 replaces these hand-written types with generated OpenAPI types so the
 * contract cannot drift.
 */

export interface LLMStatus {
  provider: string;
  model: string;
  configured: boolean;
}

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  llm: LLMStatus;
}
