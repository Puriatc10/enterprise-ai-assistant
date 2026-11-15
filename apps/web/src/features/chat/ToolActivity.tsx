import type { ToolUsage } from "../../services/api";

interface ToolActivityProps {
  tools: ToolUsage[];
}

/**
 * Backend outcome taxonomy, translated. Anything not listed falls back to the raw
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

function formatLatency(latencyMs: number): string | null {
  // No number is invented: an unmeasurable latency is simply not printed.
  if (!Number.isFinite(latencyMs)) return null;
  return `${FA_NUMBER.format(Math.round(latencyMs))}ms`;
}

export function ToolActivity({ tools }: ToolActivityProps) {
  if (tools.length === 0) return null;

  return (
    // No aria-label here: <p> maps to role=paragraph, which cannot take one. The
    // visible «ابزارها:» prefix already names the line for every reader.
    <p className="chat-tools">
      <span className="chat-tools__label">ابزارها:</span>
      {tools.map((tool, index) => {
        const label = STATUS_LABELS[tool.status] ?? tool.status;
        const latency = formatLatency(tool.latency_ms);
        const isOk = tool.status === "ok";

        return (
          <span key={`${tool.tool}-${index}`} className="chat-tools__item">
            {index > 0 ? (
              <span className="chat-tools__sep" aria-hidden="true">
                ·
              </span>
            ) : null}
            <code className="chat-ltr chat-tools__name">{tool.tool}</code>
            <span className="chat-tools__sep" aria-hidden="true">
              ·
            </span>
            <span className={isOk ? "chat-tools__status chat-tools__status--ok" : "chat-tools__status"}>
              {label}
            </span>
            {latency ? (
              <>
                <span className="chat-tools__sep" aria-hidden="true">
                  ·
                </span>
                <span className="chat-ltr chat-tools__latency">{latency}</span>
              </>
            ) : null}
          </span>
        );
      })}
    </p>
  );
}
