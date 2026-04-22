# CB Statement Diff v2

## What's new vs v1

1. **Cleaner statement extraction** — no more `<br>` soup in side-by-side panels
2. **Synchronised scrolling** — scroll one panel, the other follows
3. **Historical archive support** — compare any two statements by URL
4. **Tone tracker chart** — hawkish/dovish score across last 15+ statements, plotted as interactive SVG
5. **Market reaction overlay** — AUD/USD move after statement
6. **Statement caching** — re-runs are fast, statements only fetched once
7. **Three command modes** — recent compare, tone history, custom URL compare

## Run

### Standard — compare two most recent statements
```bash
python3 cb_diff.py
```

### Tone tracker — chart hawkish score across history
```bash
python3 cb_diff.py --tone-history --n 15
```
*Takes a few minutes on first run (scores 15 statements via Claude). Statements are cached so re-runs are fast.*

### Custom — compare any two RBA statements
```bash
python3 cb_diff.py --compare <url1> <url2>
```

## Demo flow for Mark Elworthy

1. Open your laptop
2. Run `python3 cb_diff.py --tone-history` once beforehand
3. Show the tone chart first — "Here's the RBA's hawkish/dovish trajectory over the last 15 statements"
4. Scroll to the side-by-side diff — "This is what the most recent shift actually looked like at the word level"
5. Scroll to Claude's analysis — "And here's the rates strategist read it would take a junior an hour to write"

That's the pitch.
