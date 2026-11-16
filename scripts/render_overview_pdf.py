"""Render the Persian product overview to a print-ready HTML file.

Why a converter instead of a Markdown library: the document is one file, its syntax is a
small subset, and the deployment image must not grow a dependency for a build-time script.
The output is self-contained — the font is embedded as base64 — so the PDF that Chrome
produces from it carries the Persian glyphs rather than falling back to a system font.

Usage:
    python scripts/render_overview_pdf.py            # writes build/product-overview.fa.html
"""

from __future__ import annotations

import html
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "PRODUCT-OVERVIEW.fa.md"
FONT_DIR = ROOT / "apps" / "web" / "node_modules" / "@fontsource-variable" / "vazirmatn" / "files"
OUT = ROOT / "build" / "product-overview.fa.html"

# ── inline ────────────────────────────────────────────────────────────────────


def inline(text: str) -> str:
    """Escape, then apply the inline syntax this document actually uses."""
    out = html.escape(text, quote=False)

    out = re.sub(r"`([^`]+)`", r'<code dir="ltr">\1</code>', out)
    # <https://…> autolinks, then [label](target)
    out = re.sub(
        r"&lt;(https?://[^&]+?)&gt;",
        r'<a href="\1" dir="ltr">\1</a>',
        out,
    )
    out = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        out,
    )
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out


# ── block ─────────────────────────────────────────────────────────────────────

TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")


def convert(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    index = 0
    in_code = False
    code_buffer: list[str] = []
    list_kind: str | None = None

    def close_list() -> None:
        nonlocal list_kind
        if list_kind is not None:
            out.append(f"</{list_kind}>")
            list_kind = None

    while index < len(lines):
        line = lines[index]

        # fenced code
        if line.startswith("```"):
            if in_code:
                out.append("<pre>" + html.escape("\n".join(code_buffer)) + "</pre>")
                code_buffer = []
                in_code = False
            else:
                close_list()
                in_code = True
            index += 1
            continue
        if in_code:
            code_buffer.append(line)
            index += 1
            continue

        stripped = line.strip()

        # table: header row followed by a separator row
        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and TABLE_SEPARATOR.match(lines[index + 1].strip())
        ):
            close_list()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            out.append("<table><thead><tr>")
            out.extend(f"<th>{inline(c)}</th>" for c in header)
            out.append("</tr></thead><tbody>")
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [c.strip() for c in lines[index].strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
                index += 1
            out.append("</tbody></table>")
            continue

        if not stripped:
            close_list()
            index += 1
            continue

        if stripped.startswith("---"):
            close_list()
            out.append("<hr>")
            index += 1
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if stripped.startswith(">"):
            close_list()
            # A wrapped blockquote is one paragraph: emitting a box per source line made
            # one sentence look like a stack of unrelated callouts.
            quoted: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quoted.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append(f"<blockquote>{inline(' '.join(q for q in quoted if q))}</blockquote>")
            continue

        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        ordered = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if bullet or ordered:
            kind = "ul" if bullet else "ol"
            if list_kind != kind:
                close_list()
                out.append(f"<{kind}>")
                list_kind = kind
            content = bullet.group(1) if bullet else ordered.group(2)
            out.append(f"<li>{inline(content)}</li>")
            index += 1
            continue

        close_list()
        out.append(f"<p>{inline(stripped)}</p>")
        index += 1

    close_list()
    if code_buffer:
        out.append("<pre>" + html.escape("\n".join(code_buffer)) + "</pre>")
    return "\n".join(out)


# ── template ──────────────────────────────────────────────────────────────────

PAGE = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<title>دستیار هوشمند سازمانی — معرفی محصول</title>
<style>
  /* Local file URLs, not a data URI: Chrome's print pipeline dropped the base64 font and
     silently fell back to Segoe UI, which changes the whole look of the document. The
     page is opened from disk, so relative paths resolve next to build/. */
  @font-face {{
    font-family: "Vazirmatn";
    font-style: normal;
    font-weight: 100 900;
    font-display: block;
    src: url("../apps/web/node_modules/@fontsource-variable/vazirmatn/files/vazirmatn-arabic-wght-normal.woff2") format("woff2");
    unicode-range: U+0600-06FF, U+0750-077F, U+0870-088E, U+FB50-FDFF, U+FE70-FEFF;
  }}
  @font-face {{
    font-family: "Vazirmatn";
    font-style: normal;
    font-weight: 100 900;
    font-display: block;
    src: url("../apps/web/node_modules/@fontsource-variable/vazirmatn/files/vazirmatn-latin-wght-normal.woff2") format("woff2");
    unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+2000-206F, U+2074, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215;
  }}
  @page {{ size: A4; margin: 18mm 16mm 16mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Vazirmatn", system-ui, sans-serif;
    font-size: 10.5pt; line-height: 1.85; color: #1a1d23; margin: 0;
    direction: rtl; text-align: right;
  }}
  h1 {{ font-size: 20pt; margin: 0 0 6pt; line-height: 1.35; }}
  h2 {{
    font-size: 13.5pt; margin: 18pt 0 6pt; padding-bottom: 3pt;
    border-bottom: 1.5px solid #d8dde6; color: #101318;
    break-after: avoid;
  }}
  h3 {{ font-size: 11.5pt; margin: 12pt 0 4pt; color: #26303d; break-after: avoid; }}
  p {{ margin: 0 0 6pt; }}
  /* Mixed Persian/Latin lines: derive direction per paragraph instead of forcing RTL on
     a line that begins with a filename, and keep identifiers in their own run. */
  p, li, blockquote, .cover .links div {{ unicode-bidi: plaintext; }}
  ul, ol {{ margin: 0 0 8pt; padding-inline-start: 16pt; }}
  li {{ margin-bottom: 2.5pt; }}
  strong {{ font-weight: 700; }}
  code {{
    font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
    font-size: 8.8pt; background: #f3f5f9; border: 1px solid #e3e8f0;
    border-radius: 3px; padding: 0 3px; direction: ltr; unicode-bidi: embed;
  }}
  pre {{
    font-family: ui-monospace, Consolas, monospace; font-size: 8.4pt;
    background: #f7f9fc; border: 1px solid #e3e8f0; border-radius: 6px;
    padding: 8pt 10pt; direction: ltr; text-align: left; line-height: 1.55;
    white-space: pre-wrap; break-inside: avoid; margin: 0 0 8pt;
  }}
  table {{
    width: 100%; border-collapse: collapse; margin: 4pt 0 10pt;
    font-size: 9.2pt; break-inside: avoid;
  }}
  th, td {{ border: 1px solid #dde3ec; padding: 4pt 6pt; text-align: right; vertical-align: top; }}
  th {{ background: #f2f5fa; font-weight: 700; }}
  blockquote {{
    margin: 8pt 0; padding: 7pt 10pt; border-inline-start: 3px solid #8fb0e8;
    background: #f6f9ff; color: #2b3442; font-size: 9.8pt; break-inside: avoid;
  }}
  a {{ color: #1f5fbf; text-decoration: none; }}
  a[dir="ltr"], code[dir="ltr"] {{ unicode-bidi: embed; }}
  hr {{ border: 0; border-top: 1px solid #e2e7ef; margin: 12pt 0; }}
  .cover {{
    border: 1px solid #dde3ec; border-radius: 10px; padding: 14pt 16pt;
    margin-bottom: 14pt; background: #fbfcfe;
  }}
  .cover .kicker {{ font-size: 9pt; color: #5b6678; margin: 0 0 2pt; }}
  .cover h1 {{ margin-bottom: 8pt; }}
  .cover .links {{ font-size: 9pt; line-height: 2; }}
  .cover .links b {{ color: #2b3442; }}
</style>
</head>
<body>
<div class="cover">
  <p class="kicker">سند معرفی محصول · نسخهٔ ۱.۰ · آبان ۱۴۰۵</p>
  <h1>دستیار هوشمند سازمانی</h1>
  <div class="links">
    <div><b>برنامهٔ زنده:</b> <a href="https://eaa-web-8bvb.onrender.com" dir="ltr">eaa-web-8bvb.onrender.com</a></div>
    <div><b>API:</b> <a href="https://enterprise-ai-assistant-api-jmdj.onrender.com" dir="ltr">enterprise-ai-assistant-api-jmdj.onrender.com</a></div>
    <div><b>مخزن کد:</b> <a href="https://github.com/Puriatc10/enterprise-ai-assistant" dir="ltr">github.com/Puriatc10/enterprise-ai-assistant</a></div>
  </div>
</div>
{body}
</body>
</html>
"""


def main() -> None:
    markdown = SOURCE.read_text(encoding="utf-8")
    # Drop the document's own H1 and intro pointer: the cover states both.
    markdown = re.sub(r"^# .*\n", "", markdown, count=1)
    body = convert(markdown)
    if not FONT_DIR.exists():  # pragma: no cover - setup guard
        raise SystemExit("run `npm install` in apps/web first: the font comes from there")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(PAGE.format(body=body), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} kB, Vazirmatn via local files)")


if __name__ == "__main__":
    main()
