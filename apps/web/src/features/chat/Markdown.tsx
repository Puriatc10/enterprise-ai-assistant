import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

/**
 * Render an assistant answer as markdown — safely.
 *
 * The text comes from a language model, so it is untrusted input:
 * `rehype-sanitize` runs with its **default** schema and `rehype-raw` is
 * deliberately absent, which means HTML in the answer is dropped rather than
 * parsed. The component overrides below only add attributes React controls
 * (target, rel, dir), never an HTML sink, so nothing here can re-open the hole
 * the sanitiser closed.
 *
 * Direction is a product requirement, not a detail: the UI is Persian, so the
 * prose must be RTL, while identifiers, code and tool names are Latin and must
 * read left-to-right *without* reordering the Persian around them. That is what
 * `unicode-bidi: isolate` does — a code span becomes its own bidi island, so
 * `POL-۱۲۳` inside a Persian sentence neither drags nor is dragged.
 */

const LTR_ISOLATE: CSSProperties = {
  direction: "ltr",
  unicodeBidi: "isolate",
  textAlign: "left",
};

const INLINE_CODE: CSSProperties = {
  ...LTR_ISOLATE,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
  fontSize: "0.9em",
  padding: "0.1em 0.35em",
  borderRadius: "4px",
  background: "rgba(127, 127, 127, 0.16)",
  // Inline code sits inside RTL prose: `inline-block` keeps it on one line
  // without letting a long id push the paragraph's edges around.
  display: "inline-block",
};

const COPY_RESET_MS = 1600;

/**
 * The text of a rendered node, flattened.
 *
 * A fenced block's children are React elements (`code` inside `pre`, with
 * highlighted tokens if a highlighter is ever added), so the copy button needs
 * the string, not the tree.
 */
function extractText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (node !== null && typeof node === "object" && "props" in node) {
    return extractText((node as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

interface CopyButtonProps {
  text: string;
  label: string;
  copiedLabel: string;
  className: string;
}

/** Copy with a confirmation that reverts itself; never throws at the user. */
function CopyButton({ text, label, copiedLabel, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const copy = (): void => {
    // `navigator.clipboard` is absent outside a secure context; a button that
    // silently does nothing is better than one that throws into the console.
    const clipboard = navigator.clipboard;
    if (!clipboard) return;

    void clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        if (timer.current !== null) window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => {
          setCopied(false);
        }, COPY_RESET_MS);
      })
      .catch(() => undefined);
  };

  return (
    <button type="button" className={className} onClick={copy} aria-live="polite">
      {copied ? copiedLabel : label}
    </button>
  );
}

interface CodeBlockProps {
  code: string;
  language: string | null;
}

function CodeBlock({ code, language }: CodeBlockProps) {
  return (
    <div
      dir="ltr"
      style={{
        ...LTR_ISOLATE,
        position: "relative",
        margin: "0.6em 0",
        borderRadius: "10px",
        border: "1px solid var(--border, rgba(127, 127, 127, 0.28))",
        background: "rgba(127, 127, 127, 0.1)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "0.5rem",
          padding: "0.25rem 0.5rem",
          borderBottom: "1px solid var(--border, rgba(127, 127, 127, 0.28))",
          fontSize: "0.75rem",
          color: "var(--muted, #5b6472)",
        }}
      >
        <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" }}>
          {language ?? "code"}
        </span>
        <CopyButton
          text={code}
          label="کپی"
          copiedLabel="کپی شد"
          className="md-code__copy"
        />
      </div>
      <pre
        style={{
          ...LTR_ISOLATE,
          margin: 0,
          padding: "0.7rem 0.8rem",
          overflowX: "auto",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          fontSize: "0.86rem",
          lineHeight: 1.6,
        }}
      >
        <code>{code}</code>
      </pre>
    </div>
  );
}

/**
 * Element overrides.
 *
 * A fenced block is rendered from `code` and not from `pre`, because that is
 * where the text and the language class actually are; `pre` is then reduced to
 * a passthrough so the block is not double-wrapped.
 */
const COMPONENTS: Components = {
  a({ href, children }) {
    // External links leave the app: without `noopener` the target page gets a
    // handle on this window through `window.opener`.
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },

  pre({ children }) {
    return <>{children}</>;
  },

  code({ className, children }) {
    const text = extractText(children);
    const language = /language-([\w+#-]+)/.exec(className ?? "")?.[1] ?? null;

    // No `inline` prop in react-markdown v10: a fenced block is either tagged
    // with a language or contains a newline, and an inline span never does.
    const isBlock = language !== null || text.includes("\n");
    if (!isBlock) {
      return (
        <code dir="ltr" style={INLINE_CODE}>
          {children}
        </code>
      );
    }

    return <CodeBlock code={text.replace(/\n$/, "")} language={language} />;
  },

  table({ children }) {
    // A table can be wider than the bubble; scrolling this wrapper keeps the
    // page itself from gaining a horizontal scrollbar.
    return (
      <div style={{ overflowX: "auto", margin: "0.6em 0" }}>
        <table>{children}</table>
      </div>
    );
  },
};

export interface MarkdownProps {
  /** The raw markdown of the answer. Plain text is valid markdown. */
  children: string;
}

export function Markdown({ children }: MarkdownProps) {
  return (
    <div className="md" dir="rtl" lang="fa">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={COMPONENTS}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
