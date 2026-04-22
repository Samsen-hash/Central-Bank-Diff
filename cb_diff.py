#!/usr/bin/env python3
"""
Central Bank Statement Diff Tool
─────────────────────────────────
Fetches the two most recent RBA (and optionally Fed) monetary policy statements,
performs a word-level diff, and uses Claude to interpret the tone shift — the
kind of analysis a rates desk runs in the minutes following a statement release.

Outputs an HTML report you can open in a browser or share.

Usage:
    python cb_diff.py                  # Runs RBA diff (default)
    python cb_diff.py --bank fed       # Runs Fed FOMC diff
    python cb_diff.py --bank both      # Runs both

Author: Sam Hash, 2026
"""

import os
import re
import sys
import argparse
import difflib
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import feedparser
import anthropic
import pytz
from bs4 import BeautifulSoup
import requests


# ── CONFIG ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

RBA_MEDIA_RELEASES_URL   = "https://www.rba.gov.au/media-releases/"
FED_FOMC_URL             = "https://www.federalreserve.gov/newsevents/pressreleases/monetary.htm"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36"
HEADERS    = {"User-Agent": USER_AGENT}


# ── DATA MODEL ────────────────────────────────────────────────────────────
@dataclass
class Statement:
    bank:  str    # "RBA" or "Fed"
    title: str
    date:  str
    url:   str
    text:  str


# ── FETCHERS ──────────────────────────────────────────────────────────────
def fetch_rba_statements(n: int = 2) -> list[Statement]:
    """Scrape the RBA media releases page for the N most recent monetary
    policy decisions (filtered by title)."""
    print("→ Fetching RBA media releases index...")
    r = requests.get(RBA_MEDIA_RELEASES_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    statements = []
    # RBA media releases are structured as <a> tags within <li> elements
    for link in soup.find_all("a", href=True):
        href  = link.get("href", "")
        title = link.get_text(strip=True)
        if "Monetary Policy Decision" in title and "/media-releases/" in href:
            full_url = urljoin("https://www.rba.gov.au", href)
            if full_url not in {s.url for s in statements}:
                statements.append(Statement(
                    bank="RBA",
                    title=title,
                    date="",
                    url=full_url,
                    text="",
                ))
        if len(statements) >= n:
            break

    # Fetch the body text for each
    for s in statements:
        print(f"→ Fetching {s.url}")
        r = requests.get(s.url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        page = BeautifulSoup(r.text, "html.parser")

        # Extract date
        date_el = page.find("p", class_="rba-date") or page.find("time")
        if date_el:
            s.date = date_el.get_text(strip=True)

        # Extract the statement body — RBA wraps it in <div id="content">
        content_div = page.find("div", id="content") or page.find("main")
        if content_div:
            # Keep paragraphs only
            paragraphs = [p.get_text(" ", strip=True) for p in content_div.find_all("p")]
            # Filter out boilerplate / signature lines
            body = "\n\n".join(p for p in paragraphs if len(p) > 40)
            s.text = body
        else:
            s.text = page.get_text("\n", strip=True)

    return statements


def fetch_fed_statements(n: int = 2) -> list[Statement]:
    """Scrape Fed FOMC statements."""
    print("→ Fetching Fed FOMC press releases...")
    r = requests.get(FED_FOMC_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    statements = []
    for link in soup.find_all("a", href=True):
        href  = link.get("href", "")
        title = link.get_text(strip=True)
        if "monetary" in href.lower() and "a.htm" in href.lower():
            full_url = urljoin("https://www.federalreserve.gov", href)
            if full_url not in {s.url for s in statements}:
                statements.append(Statement(
                    bank="Fed",
                    title=title or "FOMC Statement",
                    date="",
                    url=full_url,
                    text="",
                ))
        if len(statements) >= n:
            break

    for s in statements:
        print(f"→ Fetching {s.url}")
        r = requests.get(s.url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        page = BeautifulSoup(r.text, "html.parser")
        paragraphs = [p.get_text(" ", strip=True) for p in page.find_all("p")]
        body = "\n\n".join(p for p in paragraphs if len(p) > 40)
        s.text = body

    return statements


# ── DIFF ──────────────────────────────────────────────────────────────────
def word_diff_html(old: str, new: str) -> str:
    """Return an HTML-formatted word-level diff.
    Removed words: red strikethrough. Added words: green underlined."""
    old_words = re.findall(r"\S+|\s+", old)
    new_words = re.findall(r"\S+|\s+", new)

    matcher = difflib.SequenceMatcher(None, old_words, new_words)
    html_parts = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            html_parts.append("".join(new_words[j1:j2]))
        elif tag == "replace":
            removed = "".join(old_words[i1:i2])
            added   = "".join(new_words[j1:j2])
            html_parts.append(f'<del style="background:#ffd7d7;color:#a00;text-decoration:line-through;">{removed}</del>')
            html_parts.append(f'<ins style="background:#d7f5d7;color:#060;text-decoration:none;font-weight:600;">{added}</ins>')
        elif tag == "delete":
            removed = "".join(old_words[i1:i2])
            html_parts.append(f'<del style="background:#ffd7d7;color:#a00;text-decoration:line-through;">{removed}</del>')
        elif tag == "insert":
            added = "".join(new_words[j1:j2])
            html_parts.append(f'<ins style="background:#d7f5d7;color:#060;text-decoration:none;font-weight:600;">{added}</ins>')

    return "".join(html_parts).replace("\n", "<br>")


# ── CLAUDE ANALYSIS ───────────────────────────────────────────────────────
def analyse_shift(old: Statement, new: Statement) -> str:
    """Ask Claude to interpret the tone shift between two statements."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are a senior rates strategist. Two consecutive monetary policy statements from the {new.bank} are provided below. Analyse the language shift like you would on a trading desk minutes after release.

Produce a tight analyst note in this exact structure:

HEADLINE READ
[One sentence: overall hawkish / dovish / neutral lean of the shift, and the magnitude.]

KEY LANGUAGE CHANGES
[4-6 bullet points covering the most material wording changes. For each, quote the specific change briefly and explain what it signals. Focus on: inflation assessment, labour market, growth outlook, policy path guidance, risk assessment.]

WHAT'S BEEN DROPPED
[Any phrases that were in the previous statement but have been removed. Often more telling than what was added.]

WHAT'S BEEN ADDED
[New phrasing the Board/Committee has introduced.]

MARKET IMPLICATIONS
[3-4 bullet points: likely impact on the short end of the curve, long end, FX, equities. Be specific about direction and reasoning.]

TRADING DESK TAKE
[2-3 sentences written like a senior rates strategist talking to a PM. What's the actionable view?]

─────────────────────────
PREVIOUS STATEMENT ({old.date} — {old.title}):
{old.text}

─────────────────────────
NEW STATEMENT ({new.date} — {new.title}):
{new.text}
"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


# ── REPORT BUILDER ────────────────────────────────────────────────────────
def build_html_report(statements: list[Statement], analysis: str) -> str:
    """Compose the final HTML output."""
    old, new = statements[1], statements[0]  # statements[0] is most recent
    diff_html = word_diff_html(old.text, new.text)

    # Convert analysis sections to HTML
    analysis_html_lines = []
    for line in analysis.split("\n"):
        stripped = line.strip()
        if not stripped:
            analysis_html_lines.append("<br>")
        elif stripped.isupper() and len(stripped) < 60:
            analysis_html_lines.append(f'<h3 style="margin:22px 0 6px 0;font-size:13px;letter-spacing:1px;color:#0b3d91;">{stripped}</h3>')
        elif stripped.startswith("─"):
            continue
        elif stripped.startswith("•") or stripped.startswith("-"):
            analysis_html_lines.append(f'<p style="margin:4px 0 4px 16px;">{stripped}</p>')
        else:
            analysis_html_lines.append(f'<p style="margin:6px 0;">{stripped}</p>')

    now_str = datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y, %H:%M AEST")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{new.bank} Statement Diff — {new.date}</title>
<style>
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #222; max-width: 1100px; margin: 30px auto; padding: 0 24px; line-height: 1.55; }}
  .header {{ border-bottom: 3px solid #0b3d91; padding-bottom: 16px; margin-bottom: 28px; }}
  h1 {{ font-size: 22px; margin: 0; color: #0b3d91; }}
  h2 {{ font-size: 16px; margin-top: 36px; color: #0b3d91; border-bottom: 1px solid #ddd; padding-bottom: 6px; }}
  .meta {{ color: #666; font-size: 12px; margin-top: 4px; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 14px; }}
  .col {{ background: #fafafa; border: 1px solid #e0e0e0; padding: 16px 20px; border-radius: 4px; font-size: 13px; max-height: 420px; overflow-y: auto; }}
  .col h4 {{ margin: 0 0 10px 0; font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 1px; }}
  .diff-box {{ background: #fff; border: 1px solid #ccc; padding: 22px 26px; border-radius: 4px; font-size: 13.5px; line-height: 1.75; margin-top: 14px; }}
  .analysis-box {{ background: #f5f8fd; border-left: 4px solid #0b3d91; padding: 18px 24px; margin-top: 14px; font-size: 13.5px; }}
  .legend {{ font-size: 11px; color: #666; margin-top: 10px; }}
  .legend span {{ padding: 2px 6px; border-radius: 3px; margin-right: 8px; }}
  footer {{ margin-top: 50px; font-size: 10px; color: #999; border-top: 1px solid #eee; padding-top: 14px; }}
</style>
</head>
<body>

<div class="header">
  <h1>{new.bank} Statement Diff — {new.date or "Most recent"}</h1>
  <p class="meta">Generated {now_str} · Comparing {old.date or "previous"} vs {new.date or "current"}</p>
</div>

<h2>Side-by-side Statements</h2>
<div class="two-col">
  <div class="col">
    <h4>Previous — {old.date or old.title}</h4>
    <div>{old.text.replace(chr(10), '<br>')}</div>
  </div>
  <div class="col">
    <h4>Current — {new.date or new.title}</h4>
    <div>{new.text.replace(chr(10), '<br>')}</div>
  </div>
</div>

<h2>Word-level Diff</h2>
<p class="legend">
  <span style="background:#ffd7d7;color:#a00;text-decoration:line-through;">removed</span>
  <span style="background:#d7f5d7;color:#060;font-weight:600;">added</span>
</p>
<div class="diff-box">{diff_html}</div>

<h2>Claude Analysis — Rates Strategist Read</h2>
<div class="analysis-box">
  {''.join(analysis_html_lines)}
</div>

<footer>
Built by Sam Hash · Inspired by rates desk workflows at BofA, Barclays, Goldman Sachs · Sources: rba.gov.au, federalreserve.gov · Analysis generated by Claude.
</footer>

</body>
</html>
"""
    return html


# ── MAIN ──────────────────────────────────────────────────────────────────
def run(bank: str):
    if bank == "rba":
        statements = fetch_rba_statements(n=2)
    elif bank == "fed":
        statements = fetch_fed_statements(n=2)
    else:
        raise ValueError(f"Unknown bank: {bank}")

    if len(statements) < 2:
        print(f"Need at least 2 statements, found {len(statements)}.")
        sys.exit(1)

    print(f"\n✅ Found {len(statements)} statements.")
    for s in statements:
        print(f"  · {s.date} — {s.title}")

    print("\n→ Asking Claude for strategist analysis...")
    analysis = analyse_shift(statements[1], statements[0])

    print("\n→ Building HTML report...")
    html = build_html_report(statements, analysis)

    # Save output
    os.makedirs("output", exist_ok=True)
    today = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"output/{bank}_diff_{today}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Report saved: {filename}")
    print(f"   Open it in your browser to view.\n")

    print("\n" + "─" * 60)
    print("ANALYSIS PREVIEW")
    print("─" * 60)
    print(analysis)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Central Bank Statement Diff Tool")
    parser.add_argument("--bank", default="rba", choices=["rba", "fed", "both"],
                        help="Which central bank to analyse")
    args = parser.parse_args()

    if args.bank == "both":
        for b in ("rba", "fed"):
            print(f"\n{'=' * 60}\n  {b.upper()}\n{'=' * 60}")
            run(b)
    else:
        run(args.bank)
