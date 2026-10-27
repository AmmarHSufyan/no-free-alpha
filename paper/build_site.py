"""Build the paper's web page (GitHub Pages, served from docs/) and, optionally, its PDF.

  pip install markdown
  python paper/build_site.py            # docs/index.html + docs/figures/
  python paper/build_site.py --pdf      # also docs/no-free-alpha.pdf via a headless Chrome/Edge

The page carries the metadata search engines and scholarly indexes read: description and keywords, canonical
URL, Open Graph / Twitter cards, Google Scholar `citation_*` tags and schema.org ScholarlyArticle JSON-LD.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
PAPER, DOCS = ROOT / "paper", ROOT / "docs"

SITE = "https://ammarhsufyan.github.io/no-free-alpha/"
REPO = "https://github.com/AmmarHSufyan/no-free-alpha"
AUTHOR = "AmmarHSufyan"
DATE = "2026-10-27"
TITLE = ("No Free Alpha: Transaction Costs, Luck, and the Limits of Algorithmic, LLM-Driven and Copy Trading "
         "for Retail Crypto Investors")
PAGE_TITLE = "No Free Alpha: Do AI Trading Bots and Copy Trading Beat Holding Bitcoin? (2026 study)"
DESCRIPTION = ("A cost-aware 2026 study of AI/LLM trading agents, crypto trading bots, Freqtrade strategies and "
               "Hyperliquid copy trading across 1.5 million wallets. After fees, none beat holding Bitcoin.")
KEYWORDS = ["AI trading", "LLM trading agent", "Claude trading bot", "ChatGPT trading bot", "crypto trading bot",
            "algorithmic trading", "backtesting", "transaction costs", "trading fees", "copy trading",
            "social trading", "Hyperliquid", "smart money", "on-chain analytics", "whale tracking",
            "skill versus luck", "false discovery rate", "walk-forward analysis", "Freqtrade", "trend following",
            "Bitcoin", "cryptocurrency", "quantitative finance", "retail traders"]
IMAGE = SITE + "figures/fig2_copy_walkforward.png"

CSS = """
:root { --bg:#fcfcfb; --fg:#1b1b1a; --muted:#5d5c58; --rule:#e1e0d9; --accent:#2a78d6; --code:#f1f0ec; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1a1a19; --fg:#ecebe7; --muted:#b5b4ad; --rule:#33332f; --accent:#6da7ec; --code:#262624; }
  img { background:#fcfcfb; border-radius:4px; }
}
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:17px/1.65 Charter, "Bitstream Charter", "Sitka Text", Cambria, Georgia, serif; }
main { max-width:46rem; margin:0 auto; padding:2.5rem 1rem 4rem; }
h1 { font-size:1.9rem; line-height:1.25; margin:0 0 .6rem; }
h2 { font-size:1.35rem; margin:2.6rem 0 .8rem; padding-top:.6rem; border-top:1px solid var(--rule); }
h3 { font-size:1.12rem; margin:2rem 0 .6rem; }
h4 { font-size:1rem; margin:1.6rem 0 .5rem; }
h1, h2, h3, h4, nav, table, figcaption, .meta { font-family:system-ui, -apple-system, "Segoe UI", sans-serif; }
p, li { hyphens:auto; }
a { color:var(--accent); }
hr { border:0; border-top:1px solid var(--rule); margin:2rem 0; }
img { max-width:100%; height:auto; display:block; margin:1.2rem auto; }
.table-wrap { overflow-x:auto; margin:1rem 0; }
table { border-collapse:collapse; font-size:.86rem; line-height:1.4; width:100%; }
th, td { padding:.4rem .55rem; border-bottom:1px solid var(--rule); text-align:left; vertical-align:top; }
th { font-weight:600; border-bottom:2px solid var(--rule); }
code { font:.86em ui-monospace, SFMono-Regular, Consolas, monospace; background:var(--code); padding:.08em .3em; border-radius:3px; }
pre { background:var(--code); padding:.8rem 1rem; overflow-x:auto; border-radius:4px; }
pre code { padding:0; background:none; }
.links { font-family:system-ui, -apple-system, "Segoe UI", sans-serif; font-size:.92rem; margin:1rem 0 0; }
.links a { margin-right:1rem; }
ol li { margin:.25rem 0; }
@media print {
  @page { size:A4; margin:16mm 15mm; }
  :root { --bg:#fff; --fg:#000; --muted:#333; --rule:#ccc; --accent:#1c5cab; --code:#f3f3f3; }
  body { font-size:10.5pt; }
  main { max-width:none; padding:0; }
  .links { display:none; }
  h2 { break-before:auto; break-after:avoid; }
  h3, h4 { break-after:avoid; }
  img, table, pre { break-inside:avoid; }
  a { color:inherit; text-decoration:none; }
}
"""


def head(abstract: str) -> str:
    def meta(name, content, attr="name"):
        return f'<meta {attr}="{name}" content="{html.escape(content, quote=True)}">'

    ld = {
        "@context": "https://schema.org", "@type": "ScholarlyArticle", "headline": TITLE, "name": TITLE,
        "author": {"@type": "Person", "name": AUTHOR, "url": f"https://github.com/{AUTHOR}"},
        "datePublished": DATE, "inLanguage": "en", "isAccessibleForFree": True, "url": SITE, "image": IMAGE,
        "abstract": abstract, "keywords": ", ".join(KEYWORDS), "license": "https://opensource.org/licenses/MIT",
        "encoding": {"@type": "MediaObject", "contentUrl": SITE + "no-free-alpha.pdf", "encodingFormat": "application/pdf"},
        "isBasedOn": {"@type": "SoftwareSourceCode", "codeRepository": REPO, "programmingLanguage": "Python"},
        "about": ["Algorithmic trading", "Copy trading", "Large language models", "Cryptocurrency", "Backtesting"],
    }
    tags = [
        '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(PAGE_TITLE)}</title>",
        meta("description", DESCRIPTION), meta("keywords", ", ".join(KEYWORDS)), meta("author", AUTHOR),
        meta("robots", "index, follow"), f'<link rel="canonical" href="{SITE}">',
        # Open Graph / Twitter (link previews)
        meta("og:type", "article", "property"), meta("og:title", PAGE_TITLE, "property"),
        meta("og:description", DESCRIPTION, "property"), meta("og:url", SITE, "property"),
        meta("og:image", IMAGE, "property"), meta("og:site_name", "No Free Alpha", "property"),
        meta("article:published_time", DATE, "property"),
        meta("twitter:card", "summary_large_image"), meta("twitter:title", PAGE_TITLE),
        meta("twitter:description", DESCRIPTION), meta("twitter:image", IMAGE),
        # Google Scholar (Highwire Press tags)
        meta("citation_title", TITLE), meta("citation_author", AUTHOR),
        meta("citation_publication_date", DATE.replace("-", "/")), meta("citation_online_date", DATE.replace("-", "/")),
        meta("citation_technical_report_institution", "Independent research"),
        meta("citation_language", "en"), meta("citation_abstract", abstract),
        meta("citation_pdf_url", SITE + "no-free-alpha.pdf"), meta("citation_fulltext_html_url", SITE),
        *[meta("citation_keywords", k) for k in KEYWORDS],
        f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>',
        f"<style>{CSS}</style>",
    ]
    return "\n".join(tags)


def build() -> None:
    src = (PAPER / "no-free-alpha.md").read_text(encoding="utf-8")
    m = re.search(r"## Abstract\n\n(.*?)\n\n\*\*Keywords", src, re.S)
    abstract = " ".join(re.sub(r"[*`]", "", m.group(1)).split()) if m else DESCRIPTION
    body = markdown.markdown(src, extensions=["tables", "fenced_code", "toc", "sane_lists"], output_format="html5")
    body = re.sub(r"<table>", '<div class="table-wrap"><table>', body).replace("</table>", "</table></div>")
    body = body.replace("<hr>\n", "")  # section headings already carry a rule
    body = re.sub(r'<img alt="(Figure \d+)" src="([^"]+)" ?/?>', r'<img alt="\1" src="\2" loading="lazy">', body)
    body = re.sub(r'(?<![="\'>])(https?://[^\s<]+?)([.,;)]?)(?=[\s<])', r'<a href="\1">\1</a>\2', body)  # bare URLs
    links = (f'<p class="links"><a href="no-free-alpha.pdf">PDF</a><a href="{REPO}">Code and data (GitHub)</a>'
             f'<a href="{REPO}/blob/main/paper/no-free-alpha.md">Markdown</a></p>')
    body = body.replace("</h1>", "</h1>" + links, 1)
    page = f'<!doctype html>\n<html lang="en">\n<head>\n{head(abstract)}\n</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n'
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8", newline="\n")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copytree(PAPER / "figures", DOCS / "figures", dirs_exist_ok=True)
    print("wrote", DOCS / "index.html")


def pdf() -> None:
    browsers = [shutil.which(b) for b in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge")]
    browsers += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    exe = next((b for b in browsers if b and Path(b).exists()), None)
    if exe is None:
        sys.exit("no Chrome/Edge/Chromium found for printing the PDF")
    out = DOCS / "no-free-alpha.pdf"
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
                    f"--print-to-pdf={out}", (DOCS / "index.html").as_uri()], check=True, timeout=120,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:  # replace the browser's metadata (its user agent names the OS) with the paper's own
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("pypdf not installed: PDF metadata left as the browser wrote it")
    else:
        w = PdfWriter(clone_from=PdfReader(out))
        w.add_metadata({"/Title": TITLE, "/Author": AUTHOR, "/Subject": DESCRIPTION, "/Keywords": ", ".join(KEYWORDS),
                        "/Creator": "paper/build_site.py", "/Producer": "Chromium print-to-PDF"})
        with open(out, "wb") as f:
            w.write(f)
    print("wrote", out, f"({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build()
    if "--pdf" in sys.argv:
        pdf()
