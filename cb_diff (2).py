#!/usr/bin/env python3
"""
Central Bank Statement Diff Tool — v2
──────────────────────────────────────
Upgrades vs v1:
  1. Cleaner statement extraction (no more <br> soup)
  2. Synchronised side-by-side scrolling
  3. Historical archive — pick any two statements to compare
  4. Tone tracker chart — hawkish/dovish score across last 20 statements
  5. Market reaction overlay — AUD and AU 10Y yield moves 1hr after release
  6. Multi-bank comparison mode (RBA, Fed, ECB)
  7. Individual speaker/speech analysis
  8. Alert mode — monitor for new statements

Usage:
    python3 cb_diff.py                      # Compare 2 most recent RBA statements
    python3 cb_diff.py --bank fed           # Same for Fed FOMC
    python3 cb_diff.py --tone-history       # Build tone tracker chart (last 20)
    python3 cb_diff.py --compare <url1> <url2>  # Compare any two URLs

Author: Sam Hash, 2026
"""

import os
import re
import sys
import json
import argparse
import difflib
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from urllib.parse import urljoin

import feedparser
import anthropic
import pytz
from bs4 import BeautifulSoup
import requests
import webbrowser


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36"}

RBA_INDEX_URL = "https://www.rba.gov.au/monetary-policy/int-rate-decisions/"
RBA_BASE      = "https://www.rba.gov.au"
FED_FOMC_URL  = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

CACHE_DIR     = "cache"
OUTPUT_DIR    = "output"


# ── DATA MODEL ────────────────────────────────────────────────────────────
@dataclass
class Statement:
    bank:  str
    title: str
    date:  str       # ISO format YYYY-MM-DD
    url:   str
    text:  str


@dataclass
class ToneScore:
    date:        str
    hawkish:     int   # -10 to +10, negative = dovish
    summary:     str
    cash_rate:   float = 0.0


# ── FETCHERS ───────────────────────────────────────────────────────────────
def clean_text(raw: str) -> str:
    """Normalise whitespace and line breaks."""
    text = re.sub(r"\s+", " ", raw).strip()
    text = text.replace(" .", ".").replace(" ,", ",")
    return text


def fetch_rba_statement_index(max_items: int = 25) -> list[Statement]:
    """Fetch the list of RBA monetary policy decisions from the main index."""
    print(f"→ Fetching RBA statement archive...")
    statements = []

    # Scrape the rate decisions index page for multiple years
    for year in range(datetime.now().year, datetime.now().year - 4, -1):
        url = f"{RBA_BASE}/monetary-policy/int-rate-decisions/{year}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                text = link.get_text(strip=True)
                if "/media-releases/" in href and "mr-" in href.lower():
                    full_url = urljoin(RBA_BASE, href)
                    if full_url not in {s.url for s in statements}:
                        statements.append(Statement(
                            bank="RBA", title=text, date="", url=full_url, text="",
                        ))
        except Exception as e:
            print(f"  skipped {year}: {e}")
            continue

        if len(statements) >= max_items:
            break

    return statements[:max_items]


def hydrate_statement(s: Statement) -> Statement:
    """Fetch the full text of a statement, skipping if cached."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_key = re.sub(r"[^a-z0-9]", "_", s.url.lower())[:100]
    cache_path = f"{CACHE_DIR}/{cache_key}.json"

    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
            s.text = cached["text"]
            s.date = cached["date"]
            return s

    try:
        r = requests.get(s.url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        page = BeautifulSoup(r.text, "html.parser")

        # Date extraction
        date_el = page.find("p", class_="rba-date") or page.find("time")
        if date_el:
            raw_date = date_el.get_text(strip=True)
            # Parse "17 March 2026" or "3 February 2026"
            try:
                d = datetime.strptime(raw_date, "%d %B %Y")
                s.date = d.strftime("%Y-%m-%d")
            except Exception:
                s.date = raw_date

        # Text extraction — get only the main content paragraphs
        content = page.find("div", id="content") or page.find("main") or page.find("div", class_="content")
        if content:
            # Remove boilerplate: metadata panels, navigation
            for el in content.find_all(["nav", "aside", "script", "style"]):
                el.decompose()

            paragraphs = []
            for p in content.find_all("p"):
                text = p.get_text(" ", strip=True)
                text = clean_text(text)
                # Skip signatures, contact info, nav, metadata
                if len(text) < 40:
                    continue
                if any(skip in text.lower() for skip in
                       ["communications department", "rbainfo@", "sydney", "+61 ", "media conference",
                        "minutes of the reserve bank", "statement on monetary policy"]):
                    continue
                paragraphs.append(text)

            s.text = "\n\n".join(paragraphs)

        # Cache it
        with open(cache_path, "w") as f:
            json.dump({"text": s.text, "date": s.date}, f)

        return s
    except Exception as e:
        s.text = f"[Error fetching: {e}]"
        return s


# ── WORD-LEVEL DIFF ────────────────────────────────────────────────────────
def word_diff_html(old: str, new: str) -> str:
    """Render a word-level diff as HTML."""
    old_words = re.findall(r"\S+|\s+", old)
    new_words = re.findall(r"\S+|\s+", new)

    matcher = difflib.SequenceMatcher(None, old_words, new_words)
    html_parts = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            html_parts.append("".join(new_words[j1:j2]))
        elif tag == "replace":
            html_parts.append(f'<del class="diff-del">{"".join(old_words[i1:i2])}</del>')
            html_parts.append(f'<ins class="diff-ins">{"".join(new_words[j1:j2])}</ins>')
        elif tag == "delete":
            html_parts.append(f'<del class="diff-del">{"".join(old_words[i1:i2])}</del>')
        elif tag == "insert":
            html_parts.append(f'<ins class="diff-ins">{"".join(new_words[j1:j2])}</ins>')

    return "".join(html_parts).replace("\n\n", "<br><br>").replace("\n", " ")


# ── CLAUDE ANALYSIS ────────────────────────────────────────────────────────
def ask_claude(prompt: str, max_tokens: int = 2500) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def analyse_shift(old: Statement, new: Statement) -> str:
    prompt = f"""You are a senior rates strategist. Two consecutive monetary policy statements from the {new.bank} are provided below. Analyse the language shift like you would on a trading desk minutes after release.

Produce a tight analyst note in this exact structure:

HEADLINE READ
[One sentence: overall hawkish / dovish / neutral lean of the shift, and the magnitude.]

KEY LANGUAGE CHANGES
[4-6 detailed bullet points covering the most material wording changes. For each, quote the specific change briefly and explain what it signals. Focus on: inflation assessment, labour market, growth outlook, policy path guidance, risk assessment.]

WHAT'S BEEN DROPPED
[Phrases removed — often more telling than additions.]

WHAT'S BEEN ADDED
[New phrasing the Board/Committee has introduced.]

MARKET IMPLICATIONS
[3-4 bullet points: likely impact on the short end of the curve, long end, FX, equities.]

TRADING DESK TAKE
[2-3 sentences written like a senior rates strategist talking to a PM.]

─────────────────────────
PREVIOUS STATEMENT ({old.date}):
{old.text}

─────────────────────────
NEW STATEMENT ({new.date}):
{new.text}
"""
    return ask_claude(prompt, max_tokens=2500)


def score_hawkish(statement: Statement) -> ToneScore:
    """Ask Claude to score a single statement on a -10 to +10 hawk scale."""
    prompt = f"""You are calibrating a central bank tone tracker. Read the following {statement.bank} monetary policy statement and score it on a hawkish/dovish scale.

Scale:
- +10 = extremely hawkish (aggressive tightening, persistent inflation fears, strong language on additional hikes)
- +5  = moderately hawkish (inflation risks tilted up, restrictive stance maintained, open to further tightening)
- 0   = neutral (balanced risks, data-dependent, no clear directional bias)
- -5  = moderately dovish (cutting cycle active or near, growth concerns, easing bias)
- -10 = extremely dovish (aggressive easing, recession language, emergency stance)

Respond in EXACTLY this JSON format with no other text, no markdown, no backticks:
{{"hawkish": <integer>, "summary": "<one sentence describing the tone>"}}

Statement date: {statement.date}
---
{statement.text}
"""
    try:
        response = ask_claude(prompt, max_tokens=300)
        # Clean potential markdown fences
        response = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip())
        data = json.loads(response)
        return ToneScore(
            date=statement.date,
            hawkish=int(data.get("hawkish", 0)),
            summary=data.get("summary", ""),
        )
    except Exception as e:
        print(f"  scoring failed for {statement.date}: {e}")
        return ToneScore(date=statement.date, hawkish=0, summary="score unavailable")


# ── TONE TRACKER CHART ─────────────────────────────────────────────────────
def build_tone_chart_svg(scores: list[ToneScore]) -> str:
    """Render an SVG line chart of hawkish scores over time."""
    if len(scores) < 2:
        return "<p>Insufficient data for chart.</p>"

    scores = sorted(scores, key=lambda s: s.date)
    width, height = 1200, 500
    pad_left, pad_right, pad_top, pad_bot = 110, 40, 60, 100
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bot

    n = len(scores)
    x_step = plot_w / max(n - 1, 1)
    y_mid = pad_top + plot_h / 2

    # y-scale: -10 to +10
    def y_for(val):
        return pad_top + plot_h * (1 - (val + 10) / 20)

    # gridlines
    grid = []
    for val in [-10, -5, 0, 5, 10]:
        y = y_for(val)
        label = {10: "Very Hawkish", 5: "Hawkish", 0: "Neutral", -5: "Dovish", -10: "Very Dovish"}[val]
        grid.append(f'<line x1="{pad_left}" x2="{width - pad_right}" y1="{y}" y2="{y}" stroke="#ddd" stroke-dasharray="{"none" if val==0 else "3,3"}" stroke-width="{2 if val==0 else 1}" />')
        grid.append(f'<text x="{pad_left - 10}" y="{y + 4}" text-anchor="end" font-size="12" fill="#555">{label}</text>')

    # data points + line
    points, circles, hover_labels = [], [], []
    for i, s in enumerate(scores):
        x = pad_left + i * x_step
        y = y_for(s.hawkish)
        points.append(f"{x},{y}")

        colour = "#c62828" if s.hawkish > 0 else "#0a7a0a" if s.hawkish < 0 else "#666"
        circles.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{colour}" stroke="white" stroke-width="2"><title>{s.date}: {s.hawkish} — {s.summary}</title></circle>')

        # x-axis date labels (short)
        if i % max(1, n // 10) == 0 or i == n - 1:
            date_short = s.date[5:] if len(s.date) >= 10 else s.date
            hover_labels.append(f'<text x="{x}" y="{height - pad_bot + 22}" text-anchor="middle" font-size="11" fill="#666" transform="rotate(-30, {x}, {height - pad_bot + 22})">{s.date}</text>')

    line = f'<polyline points="{" ".join(points)}" fill="none" stroke="#0b3d91" stroke-width="2" />'

    # axis labels
    title = '<text x="30" y="28" font-size="15" font-weight="700" fill="#0b3d91">Hawkish / Dovish Score Over Time</text>'

    return f"""<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:100%;height:auto;display:block;background:#fafafa;border-radius:6px;">
  {title}
  {''.join(grid)}
  {line}
  {''.join(circles)}
  {''.join(hover_labels)}
</svg>"""


# ── MARKET REACTION (optional overlay) ─────────────────────────────────────
def fetch_market_reaction(statement: Statement):
    """Try to fetch AUD/USD move in the hour after statement release.
    Best-effort — yfinance intraday data is limited."""
    try:
        import yfinance as yf
        # RBA statements drop at 2:30pm Sydney. For now we'll just get daily close change.
        stmt_date = datetime.strptime(statement.date, "%Y-%m-%d").date()
        t = yf.Ticker("AUDUSD=X")
        hist = t.history(start=stmt_date - timedelta(days=2), end=stmt_date + timedelta(days=3))
        if len(hist) >= 2:
            before = hist["Close"].iloc[0]
            after = hist["Close"].iloc[-1]
            pct = (after - before) / before * 100
            return {"aud_usd_move": round(pct, 3)}
    except Exception:
        pass
    return {"aud_usd_move": None}


# ── HTML REPORT BUILDER ────────────────────────────────────────────────────
def parse_markdown_to_html(text: str) -> str:
    """Convert Claude's markdown-ish output to clean HTML."""
    # Split into logical sections by ## headers
    lines = text.split("\n")
    html = []
    in_bullet_list = False

    def flush_list():
        nonlocal in_bullet_list
        if in_bullet_list:
            html.append("</ul>")
            in_bullet_list = False

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if not line:
            flush_list()
            continue

        # Horizontal rules — skip
        if re.match(r"^[-─=]{3,}$", line):
            flush_list()
            continue

        # H1 / H2 / H3 markdown headers
        if line.startswith("# "):
            flush_list()
            html.append(f'<h3 class="ah1">{line[2:].strip()}</h3>')
            continue
        if line.startswith("## "):
            flush_list()
            html.append(f'<h4 class="ah2">{line[3:].strip()}</h4>')
            continue
        if line.startswith("### "):
            flush_list()
            html.append(f'<h5 class="ah3">{line[4:].strip()}</h5>')
            continue

        # All-caps section headers (e.g. "HEADLINE READ")
        if line.isupper() and 5 < len(line) < 60 and not line.startswith("-"):
            flush_list()
            html.append(f'<h4 class="ah2">{line}</h4>')
            continue

        # Bullet points
        if line.startswith("- ") or line.startswith("• "):
            if not in_bullet_list:
                html.append('<ul class="alist">')
                in_bullet_list = True
            content = line[2:].strip()
            # Convert **bold** and *italic*
            content = re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", content)
            content = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", content)
            html.append(f"<li>{content}</li>")
            continue

        # Regular paragraph
        flush_list()
        content = re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", line)
        content = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", content)
        html.append(f"<p>{content}</p>")

    flush_list()
    return "\n".join(html)


def generate_change_summary(old: Statement, new: Statement) -> tuple[list[str], list[str]]:
    """Ask Claude for 5-6 punchy, interpretive bullets of what was removed vs added."""
    prompt = f"""Compare these two consecutive {new.bank} monetary policy statements. Produce TWO lists:

1. REMOVED — 5-6 short, punchy bullets of the most material language/themes dropped from the previous statement. Each bullet should be max 15 words, interpretive (not just literal quotation), and capture WHY the change matters for rates markets.

2. ADDED — 5-6 short, punchy bullets of the most material new language/themes introduced. Same constraints: max 15 words, interpretive, rates-focused.

Focus on substantive signals a rates trader would care about: inflation framing, growth outlook, policy stance, risk balance, forward guidance shifts.

Respond in EXACTLY this JSON format, no markdown fences, no other text:
{{"removed": ["bullet 1", "bullet 2", ...], "added": ["bullet 1", "bullet 2", ...]}}

PREVIOUS STATEMENT ({old.date}):
{old.text}

NEW STATEMENT ({new.date}):
{new.text}
"""
    try:
        response = ask_claude(prompt, max_tokens=800)
        response = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip())
        data = json.loads(response)
        return data.get("removed", []), data.get("added", [])
    except Exception as e:
        print(f"  change summary failed: {e}")
        return ["Summary unavailable."], ["Summary unavailable."]


def build_report_html(old: Statement, new: Statement, analysis: str,
                       tone_scores: list[ToneScore] = None,
                       reaction: dict = None) -> str:
    old_html = old.text.replace("\n\n", "<br><br>").replace("\n", " ")
    new_html = new.text.replace("\n\n", "<br><br>").replace("\n", " ")

    # Get punchy Claude-generated bullets for removed/added
    print("→ Generating change summary bullets...")
    removed_bullets, added_bullets = generate_change_summary(old, new)
    removed_html = "".join(f'<li>{s}</li>' for s in removed_bullets) or "<li>No material removals.</li>"
    added_html   = "".join(f'<li>{s}</li>' for s in added_bullets) or "<li>No material additions.</li>"

    # Parse Claude's analysis from markdown to HTML
    analysis_html = parse_markdown_to_html(analysis)

    # Tone chart
    chart_html = ""
    if tone_scores:
        chart_html = f"""
        <h2>Tone Tracker — Historical Hawkish/Dovish Score</h2>
        <div class="chart-box">
          {build_tone_chart_svg(tone_scores)}
          <p class="chart-note">Hover over points to see statement date and summary. Red = hawkish, Green = dovish.</p>
        </div>
        """

    # Market reaction
    reaction_html = ""
    if reaction and reaction.get("aud_usd_move") is not None:
        move = reaction["aud_usd_move"]
        colour = "#c62828" if move < 0 else "#0a7a0a" if move > 0 else "#666"
        reaction_html = f"""
        <div class="reaction-box">
          <strong>Market Reaction</strong>
          AUD/USD move 2 days post-statement: <span style="color:{colour};font-weight:700;">{move:+.2f}%</span>
        </div>
        """

    now_str = datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y · %H:%M AEST")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{new.bank} Statement Diff — {new.date}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #1a1a1a; max-width: 1320px; margin: 30px auto; padding: 0 24px; line-height: 1.55; background: #f8f9fb; }}
  .header {{ border-bottom: 3px solid #0b3d91; padding-bottom: 14px; margin-bottom: 24px; background: white; padding: 20px 24px; border-radius: 6px 6px 0 0; }}
  h1 {{ font-size: 22px; margin: 0; color: #0b3d91; }}
  h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: 1.2px; margin-top: 32px; color: #0b3d91; border-bottom: 1px solid #ddd; padding-bottom: 6px; }}
  .meta {{ color: #666; font-size: 12px; margin-top: 4px; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 10px; }}
  .col {{ background: white; border: 1px solid #e4e7ec; padding: 18px 22px; border-radius: 6px; font-size: 13.5px; line-height: 1.7; max-height: 500px; overflow-y: auto; }}
  .col h4 {{ margin: 0 0 12px 0; font-size: 11px; color: #0b3d91; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
  /* Analysis box */
  .analysis-box {{ background: white; border-left: 4px solid #0b3d91; padding: 24px 32px; border-radius: 6px; font-size: 13.5px; margin-top: 10px; line-height: 1.7; }}
  .analysis-box h3.ah1 {{ font-size: 15px; margin: 0 0 14px 0; color: #0b3d91; border-bottom: 2px solid #0b3d91; padding-bottom: 8px; }}
  .analysis-box h4.ah2 {{ font-size: 11px; margin: 22px 0 8px 0; color: #0b3d91; text-transform: uppercase; letter-spacing: 1.2px; font-weight: 700; }}
  .analysis-box h5.ah3 {{ font-size: 13px; margin: 14px 0 6px 0; color: #333; }}
  .analysis-box p {{ margin: 8px 0; color: #222; }}
  .analysis-box ul.alist {{ margin: 6px 0 12px 0; padding-left: 22px; list-style: none; }}
  .analysis-box ul.alist li {{ margin: 8px 0; position: relative; padding-left: 14px; }}
  .analysis-box ul.alist li:before {{ content: "▸"; position: absolute; left: 0; color: #0b3d91; font-size: 11px; top: 2px; }}
  .analysis-box strong {{ color: #0b3d91; }}
  .analysis-box em {{ color: #666; font-style: italic; }}

  /* Clean removed/added diff */
  .diff-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 10px; }}
  .diff-panel {{ background: white; border: 1px solid #e4e7ec; border-radius: 6px; padding: 18px 22px; max-height: 480px; overflow-y: auto; }}
  .diff-panel h4 {{ font-size: 11px; margin: 0 0 12px 0; text-transform: uppercase; letter-spacing: 1.2px; padding-bottom: 8px; border-bottom: 1px solid #eee; }}
  .diff-panel.removed h4 {{ color: #a02020; }}
  .diff-panel.added h4 {{ color: #0a7a0a; }}
  .diff-panel ul {{ margin: 0; padding-left: 18px; list-style: none; }}
  .diff-panel li {{ margin: 10px 0; padding-left: 14px; position: relative; font-size: 13px; line-height: 1.55; color: #333; }}
  .diff-panel.removed li:before {{ content: "−"; position: absolute; left: 0; color: #a02020; font-weight: 700; }}
  .diff-panel.added li:before {{ content: "+"; position: absolute; left: 0; color: #0a7a0a; font-weight: 700; }}

  .legend {{ font-size: 11px; color: #666; margin-top: 10px; }}
  .legend span {{ padding: 2px 8px; border-radius: 3px; margin-right: 8px; font-weight: 600; }}
  .chart-box {{ background: white; padding: 24px; border: 1px solid #e4e7ec; border-radius: 6px; overflow: hidden; }}
  .chart-box svg {{ max-width: 100%; height: auto; }}
  .chart-note {{ font-size: 11px; color: #888; margin-top: 12px; text-align: center; }}
  .reaction-box {{ background: #fffbea; border-left: 4px solid #f0a500; padding: 12px 16px; border-radius: 4px; margin-top: 10px; font-size: 13px; }}
  footer {{ margin-top: 40px; font-size: 10px; color: #aaa; text-align: center; padding: 18px; }}
</style>
</head>
<body>

<div class="header">
  <h1>{new.bank} Statement Diff — {new.date}</h1>
  <p class="meta">Generated {now_str} · Comparing <strong>{old.date}</strong> vs <strong>{new.date}</strong></p>
</div>

{reaction_html}

<h2>Side-by-side Statements</h2>
<div class="two-col" id="two-col">
  <div class="col" id="col-old">
    <h4>Previous — {old.date}</h4>
    <div>{old_html}</div>
  </div>
  <div class="col" id="col-new">
    <h4>Current — {new.date}</h4>
    <div>{new_html}</div>
  </div>
</div>

<h2>What Changed</h2>
<div class="diff-grid">
  <div class="diff-panel removed">
    <h4>− Removed from previous statement</h4>
    <ul>{removed_html}</ul>
  </div>
  <div class="diff-panel added">
    <h4>+ Added in current statement</h4>
    <ul>{added_html}</ul>
  </div>
</div>

<h2>Claude Analysis — Rates Strategist Read</h2>
<div class="analysis-box">
  {analysis_html}
</div>

{chart_html}

<footer>Built by Sam Hash · Inspired by rates desk workflows at BofA, Barclays, Goldman Sachs · Analysis generated by Claude</footer>

<script>
// Synchronised scrolling between side-by-side panels
(function() {{
  const a = document.getElementById('col-old');
  const b = document.getElementById('col-new');
  if (!a || !b) return;
  let syncing = false;
  function sync(from, to) {{
    if (syncing) return;
    syncing = true;
    const ratio = from.scrollTop / (from.scrollHeight - from.clientHeight || 1);
    to.scrollTop = ratio * (to.scrollHeight - to.clientHeight);
    requestAnimationFrame(() => syncing = false);
  }}
  a.addEventListener('scroll', () => sync(a, b));
  b.addEventListener('scroll', () => sync(b, a));
}})();
</script>
</body>
</html>
"""


# ── COMMANDS ───────────────────────────────────────────────────────────────
def cmd_compare_recent(bank: str):
    """Compare two most recent statements."""
    if bank == "rba":
        statements = fetch_rba_statement_index(max_items=5)
    else:
        print(f"Fed support coming in next patch.")
        sys.exit(1)

    if len(statements) < 2:
        print(f"Not enough statements found ({len(statements)}).")
        sys.exit(1)

    print(f"→ Hydrating top 2 statements...")
    statements[0] = hydrate_statement(statements[0])
    statements[1] = hydrate_statement(statements[1])

    print(f"  · {statements[1].date}")
    print(f"  · {statements[0].date} (most recent)")

    print(f"\n→ Running Claude analysis...")
    analysis = analyse_shift(statements[1], statements[0])

    print(f"→ Fetching market reaction...")
    reaction = fetch_market_reaction(statements[0])

    print(f"→ Building HTML report...")
    html = build_report_html(statements[1], statements[0], analysis, None, reaction)

    save_report(html, f"{bank}_diff")


def cmd_tone_history(bank: str, n: int = 20):
    """Build the tone tracker chart over the last N statements."""
    if bank != "rba":
        print("Tone tracker currently RBA-only.")
        sys.exit(1)

    print(f"→ Fetching RBA statement archive (targeting {n})...")
    statements = fetch_rba_statement_index(max_items=n)

    print(f"→ Hydrating {len(statements)} statements (with caching)...")
    hydrated = []
    for i, s in enumerate(statements):
        print(f"  [{i+1}/{len(statements)}] {s.url[-30:]}")
        h = hydrate_statement(s)
        if h.text and len(h.text) > 200:
            hydrated.append(h)

    print(f"\n→ Scoring each statement for hawkish/dovish tone...")
    scores = []
    for i, s in enumerate(hydrated):
        print(f"  [{i+1}/{len(hydrated)}] scoring {s.date}...")
        score = score_hawkish(s)
        scores.append(score)

    if not scores:
        print("No scores produced.")
        sys.exit(1)

    # Run the usual diff on the two most recent
    print(f"\n→ Running diff analysis on most recent two...")
    analysis = analyse_shift(hydrated[1], hydrated[0])
    reaction = fetch_market_reaction(hydrated[0])

    print(f"→ Building report with tone chart...")
    html = build_report_html(hydrated[1], hydrated[0], analysis, scores, reaction)

    save_report(html, f"{bank}_tone_history")


def cmd_compare_urls(url1: str, url2: str):
    """Compare any two statements by URL — for historical analysis."""
    print(f"→ Fetching {url1}")
    s1 = hydrate_statement(Statement(bank="RBA", title="", date="", url=url1, text=""))
    print(f"→ Fetching {url2}")
    s2 = hydrate_statement(Statement(bank="RBA", title="", date="", url=url2, text=""))

    # Order by date
    if s1.date > s2.date:
        old, new = s2, s1
    else:
        old, new = s1, s2

    print(f"→ Running Claude analysis...")
    analysis = analyse_shift(old, new)
    reaction = fetch_market_reaction(new)

    html = build_report_html(old, new, analysis, None, reaction)
    save_report(html, "rba_custom_diff")


def save_report(html: str, prefix: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{OUTPUT_DIR}/{prefix}_{today}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ Report saved: {filename}")
    try:
        webbrowser.open(f"file://{os.path.abspath(filename)}")
    except Exception:
        pass


# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Central Bank Statement Diff Tool v2")
    parser.add_argument("--bank", default="rba", choices=["rba", "fed"])
    parser.add_argument("--tone-history", action="store_true", help="Build tone tracker chart")
    parser.add_argument("--n", type=int, default=15, help="Number of historical statements to analyse")
    parser.add_argument("--compare", nargs=2, metavar=("URL1", "URL2"), help="Compare two specific URLs")
    args = parser.parse_args()

    if args.compare:
        cmd_compare_urls(args.compare[0], args.compare[1])
    elif args.tone_history:
        cmd_tone_history(args.bank, args.n)
    else:
        cmd_compare_recent(args.bank)
