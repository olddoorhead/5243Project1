#!/usr/bin/env python3
"""
Deterministic report exporter:
1) Markdown -> HTML
2) HTML -> PDF (Playwright Chromium, A4, print backgrounds)
3) PDF page-count verification (main report <= 12 pages)
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
import sys
from pathlib import Path

import markdown
from pypdf import PdfReader


def _convert_mermaid_blocks(md_text: str) -> str:
    pattern = re.compile(r"```mermaid\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL)

    def repl(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        return f"\n<div class=\"mermaid\">{html.escape(body)}</div>\n"

    return pattern.sub(repl, md_text)


def _decorate_captions(rendered_html: str) -> str:
    # Handle captions rendered as:
    # 1) <p><em>Figure/Table ...</em></p>
    # 2) <p><img ... /><em>Figure/Table ...</em></p>
    out = re.sub(
        r"<p><em>(Figure[^<]*|Table[^<]*|Appendix Figure[^<]*|Appendix Table[^<]*)</em></p>",
        r'<p class="caption"><em>\1</em></p>',
        rendered_html,
    )
    out = re.sub(
        r"<p>(<img[^>]+/?>)\s*<em>(Figure[^<]*|Table[^<]*|Appendix Figure[^<]*|Appendix Table[^<]*)</em></p>",
        r'<p>\1</p><p class="caption"><em>\2</em></p>',
        out,
    )
    return out


def markdown_to_html(markdown_path: Path, html_path: Path, title: str, css_href: str) -> None:
    md_text = markdown_path.read_text(encoding="utf-8")
    md_text = _convert_mermaid_blocks(md_text)

    body_html = markdown.markdown(
        md_text,
        extensions=[
            "tables",
            "fenced_code",
            "sane_lists",
            "toc",
            "attr_list",
            "md_in_html",
        ],
    )
    body_html = _decorate_captions(body_html)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="{css_href}" />
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
    window.__MERMAID_READY__ = false;
    mermaid.initialize({{ startOnLoad: false, securityLevel: "loose" }});
    async function renderMermaid() {{
      const nodes = Array.from(document.querySelectorAll(".mermaid"));
      if (!nodes.length) {{
        window.__MERMAID_READY__ = true;
        return;
      }}
      for (const node of nodes) {{
        const source = node.textContent;
        const id = "mermaid-" + Math.random().toString(36).slice(2);
        try {{
          const out = await mermaid.render(id, source);
          node.innerHTML = out.svg;
        }} catch (err) {{
          console.error("Mermaid render error:", err);
        }}
      }}
      window.__MERMAID_READY__ = true;
    }}
    window.addEventListener("DOMContentLoaded", () => {{ renderMermaid(); }});
  </script>
</head>
<body>
  <main>
{body_html}
  </main>
</body>
</html>
"""

    html_path.write_text(doc, encoding="utf-8")


async def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install with `pip install playwright` and "
            "then run `python -m playwright install chromium`."
        ) from exc

    url = html_path.resolve().as_uri()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        try:
            await page.wait_for_function("window.__MERMAID_READY__ === true", timeout=12000)
        except Exception:
            # Continue if mermaid CDN is unavailable; static fallback images still render.
            pass
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
        )
        await browser.close()


def page_count(pdf_path: Path) -> int:
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)


async def run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()

    report_md = root / "Report.md"
    report_html = root / "Report.html"
    report_pdf = root / "Report.pdf"
    appendix_md = root / "Appendix.md"
    appendix_html = root / "Appendix.html"
    appendix_pdf = root / "Appendix.pdf"

    css_href = "styles/report.css"

    markdown_to_html(report_md, report_html, "Project Report", css_href)
    print(f"[build] HTML generated: {report_html}")

    markdown_to_html(appendix_md, appendix_html, "Project Appendix", css_href)
    print(f"[build] HTML generated: {appendix_html}")

    await html_to_pdf(report_html, report_pdf)
    report_pages = page_count(report_pdf)
    print(f"[build] PDF generated: {report_pdf} ({report_pages} pages)")

    if report_pages > args.report_page_limit:
        print(
            f"[error] Report page limit exceeded: {report_pages} > {args.report_page_limit}",
            file=sys.stderr,
        )
        return 2

    if args.build_appendix_pdf:
        await html_to_pdf(appendix_html, appendix_pdf)
        appendix_pages = page_count(appendix_pdf)
        print(f"[build] PDF generated: {appendix_pdf} ({appendix_pages} pages)")
    else:
        print("[build] Skipped Appendix.pdf generation.")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build report HTML/PDF artifacts.")
    parser.add_argument("--root", default=".", help="Repository root directory.")
    parser.add_argument("--report-page-limit", type=int, default=12, help="Hard page limit for Report.pdf.")
    parser.add_argument(
        "--build-appendix-pdf",
        action="store_true",
        help="Also render Appendix.pdf (no enforced limit).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    code = asyncio.run(run(args))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
