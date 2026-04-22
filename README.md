# Central Bank Statement Diff Tool

## What it does
Fetches the two most recent RBA (or Fed) monetary policy statements, runs a word-level diff, and uses Claude to interpret the tone shift — the kind of analysis rates desks run in the minutes after a statement release.

Outputs a clean HTML report with:
- Side-by-side previous vs current statements
- Word-level diff (removed in red, added in green)
- Claude's rates strategist read: headline read, language changes, what's dropped, what's added, market implications, trading desk take

## Why this matters
This is exactly the workflow major rates desks run (BofA, Barclays, Goldman, JP Morgan). Building one yourself demonstrates you understand what senior rates professionals actually do — and can build AI-powered tools to do it.

---

## Setup

### Install dependencies
```bash
pip install -r requirements.txt
```

### Set your API key
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Run it
```bash
# RBA (default)
python cb_diff.py

# Fed FOMC
python cb_diff.py --bank fed

# Both
python cb_diff.py --bank both
```

### Output
Saves an HTML file to `output/rba_diff_YYYYMMDD_HHMM.html`. Open in any browser.

---

## CV line
> *Built a Python tool that scrapes RBA/Fed monetary policy statements, performs word-level diffs, and uses Claude to generate rates-strategist-style tone shift analysis — inspired by real-world rates desk workflows. Outputs an HTML analyst report in under 60 seconds.*

## Demo flow for an interview
1. Open the HTML report on your laptop
2. Walk through the side-by-side statements
3. Show the colour-coded word diff
4. Read out Claude's tone analysis
5. Say: "Built this in an afternoon. A senior rates analyst gets this kind of read from a junior within 30-60 minutes of release. I automated the first draft."

---

## Future extensions worth adding
- Schedule it to auto-run when a new statement drops (cron + Slack/email webhook)
- Add ECB, BoJ, BoE, BoC
- Compare statement vs press conference Q&A transcripts
- Track historical tone evolution as a time series
- Integrate with bond yield data to correlate tone shifts with market moves
