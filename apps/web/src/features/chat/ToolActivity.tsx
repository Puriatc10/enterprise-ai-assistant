import type { JSX } from "react";

import type { ToolUsage } from "../../services/api";

interface ToolActivityProps {
  tools: ToolUsage[];
}

/**
 * Backend outcome taxonomy, translated. Anything unlisted falls back to the raw
 * status so a new backend value shows up as itself instead of a wrong Persian
 * word — a lying label is worse than an untranslated one.
 */
const STATUS_LABELS: Record<string, string> = {
  ok: "موفق",
  not_found: "پیدا نشد",
  forbidden: "بدون دسترسی",
  invalid_request: "درخواست نامعتبر",
  timeout: "پایان زمان",
  upstream_error: "خطای منبع",
  invalid_response: "پاسخ نامعتبر",
};

const FA_NUMBER = new Intl.NumberFormat("fa-IR");

/** No number is invented: an unmeasurable latency is simply not printed. */
function formatLatency(latencyMs: number): string | null {
  if (!Number.isFinite(latencyMs)) return null;
  return `${FA_NUMBER.format(Math.round(latencyMs))}ms`;
}

/**
 * Provenance for one answer: which tools were read, whether they answered, and
 * how long they took. It sits under the answer it belongs to, like a citation
 * list, because that is the claim it supports.
 */
export function ToolActivity({ tools }: ToolActivityProps): JSX.Element {
  // The caller decides when provenance is relevant. An empty fragment keeps the
  // contract (always an element) without drawing an empty rail.
  if (tools.length === 0) return <></>;

  return (
    <div className="tools">
      <span className="tools__label">ابزارها</span>
      <ul className="tools__list" aria-label="ابزارهای استفاده‌شده در این پاسخ">
        {tools.map((tool, index) => {
          const label = STATUS_LABELS[tool.status] ?? tool.status;
          const latency = formatLatency(tool.latency_ms);

          return (
            <li key={`${tool.tool}-${index}`} className="tool" data-state={tool.status === "ok" ? "ok" : "warn"}>
              <span className="tool__dot" aria-hidden="true" />
              <code className="tool__name ltr">{tool.tool}</code>
              <span className="tool__status">{label}</span>
              {latency !== null ? <span className="tool__latency ltr">{latency}</span> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
