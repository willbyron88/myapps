#!/usr/bin/env python3
"""
KDP Niche Scanner
Usage: python kdp_niche_scanner.py --topic "fitness journal" --max-competitors 2000
"""

import argparse
import requests
import time
import random
import re
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SUFFIXES = list("abcdefghijklmnopqrstuvwxyz0123456789")


def get_suggestions(seed: str) -> list[dict]:
    """Hit Amazon autocomplete and return ranked suggestions."""
    results = []
    for i, suffix in enumerate(SUFFIXES):
        query = f"{seed} {suffix}"
        url = "https://completion.amazon.com/api/2017/suggestions"
        params = {
            "mid": "ATVPDKIKX0DER",
            "alias": "stripbooks",
            "prefix": query,
            "limit": 10,
        }
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                data = r.json()
                for pos, s in enumerate(data.get("suggestions", [])):
                    kw = s.get("value", "").strip().lower()
                    if kw and seed.lower() in kw:
                        results.append({
                            "keyword": kw,
                            "autocomplete_position": pos + 1,
                            "suffix_letter": suffix,
                        })
        except Exception:
            pass
        time.sleep(random.uniform(0.8, 1.4))
    return results


def dedupe_keywords(raw: list[dict]) -> dict:
    """Dedupe, keep best (lowest) autocomplete position per keyword."""
    best = {}
    for item in raw:
        kw = item["keyword"]
        if kw not in best or item["autocomplete_position"] < best[kw]["autocomplete_position"]:
            best[kw] = item
    return best


def get_competition(keyword: str) -> dict:
    """Scrape Amazon Books search for competitor count and top BSR."""
    url = "https://www.amazon.com/s"
    params = {"k": keyword, "i": "stripbooks"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        text = r.text

        # Extract result count
        count_match = re.search(
            r"([\d,]+)\s*result", text, re.IGNORECASE
        ) or re.search(r"([\d,]+)\s*Result", text)
        competitor_count = 0
        if count_match:
            competitor_count = int(count_match.group(1).replace(",", ""))

        # Extract top BSR from first result if available
        bsr_match = re.search(r"Best Sellers Rank.*?#([\d,]+)", text)
        top_bsr = int(bsr_match.group(1).replace(",", "")) if bsr_match else None

        time.sleep(random.uniform(1.5, 2.5))
        return {"competitor_count": competitor_count, "top_bsr": top_bsr}
    except Exception:
        time.sleep(1)
        return {"competitor_count": 0, "top_bsr": None}


def estimate_daily_sales(bsr: int | None) -> str:
    """Rough BSR → daily sales estimate from community data."""
    if bsr is None:
        return "Unknown"
    if bsr <= 1_000:
        return "50+"
    elif bsr <= 5_000:
        return "20-50"
    elif bsr <= 10_000:
        return "10-20"
    elif bsr <= 50_000:
        return "3-10"
    elif bsr <= 100_000:
        return "1-3"
    elif bsr <= 500_000:
        return "<1"
    else:
        return "Very low"


def opportunity_score(autocomplete_position: int, competitor_count: int) -> float:
    """Score 0-100. Higher = better opportunity."""
    # Demand: position 1 = 100, position 10 = 10
    demand = max(0, 110 - (autocomplete_position * 10))
    # Competition: fewer is better; cap at 5000
    comp_score = max(0, 100 - (competitor_count / 50))
    return round((demand * 0.55) + (comp_score * 0.45), 1)


def build_html(topic: str, results: list[dict], max_competitors: int) -> str:
    rows_html = ""
    for i, r in enumerate(results, 1):
        score = r["opportunity_score"]
        color = "#22c55e" if score >= 70 else "#f59e0b" if score >= 40 else "#ef4444"
        bsr_display = f"#{r['top_bsr']:,}" if r["top_bsr"] else "N/A"
        rows_html += f"""
        <tr class="row" onclick="copyKw(this)" title="Click to copy keyword">
          <td class="rank">{i}</td>
          <td class="keyword">{r['keyword']}</td>
          <td><span class="badge" style="background:{color}">{score}</span></td>
          <td>{r['competitor_count']:,}</td>
          <td>{bsr_display}</td>
          <td>{r['est_daily_sales']}</td>
          <td>{r['autocomplete_position']}</td>
        </tr>"""

    total = len(results)
    ts = datetime.now().strftime("%B %d, %Y %I:%M %p")

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KDP Niche Scanner — {topic}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #0f0f11;
  --surface: #17171b;
  --surface-2: #1e1e24;
  --border: #2a2a35;
  --text: #e2e2e8;
  --muted: #6b6b80;
  --faint: #3a3a4a;
  --primary: #6366f1;
  --primary-glow: rgba(99,102,241,0.15);
  --green: #22c55e;
  --amber: #f59e0b;
  --red: #ef4444;
  --font: 'Inter', sans-serif;
  --mono: 'JetBrains Mono', monospace;
  --radius: 8px;
  --radius-lg: 14px;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{-webkit-font-smoothing:antialiased;scroll-behavior:smooth}}
body{{font-family:var(--font);background:var(--bg);color:var(--text);min-height:100vh;padding:2rem 1.5rem}}
.wrap{{max-width:1100px;margin:0 auto}}

/* Header */
.header{{margin-bottom:2.5rem}}
.logo{{display:flex;align-items:center;gap:0.75rem;margin-bottom:1.5rem}}
.logo svg{{color:var(--primary)}}
.logo-text{{font-size:1rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:var(--muted)}}
h1{{font-size:clamp(1.6rem,4vw,2.4rem);font-weight:700;letter-spacing:-0.03em;line-height:1.2}}
h1 span{{color:var(--primary)}}
.meta{{margin-top:0.5rem;font-size:0.85rem;color:var(--muted)}}

/* Stats bar */
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin-bottom:2rem}}
.stat{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:1.2rem 1.4rem}}
.stat-label{{font-size:0.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.4rem}}
.stat-value{{font-size:1.6rem;font-weight:700;font-family:var(--mono)}}
.stat-value.green{{color:var(--green)}}
.stat-value.amber{{color:var(--amber)}}
.stat-value.purple{{color:var(--primary)}}

/* Controls */
.controls{{display:flex;gap:0.75rem;flex-wrap:wrap;margin-bottom:1.5rem;align-items:center}}
.search-box{{flex:1;min-width:200px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:0.6rem 1rem;color:var(--text);font-family:var(--font);font-size:0.9rem;outline:none;transition:border-color 0.2s}}
.search-box:focus{{border-color:var(--primary)}}
.search-box::placeholder{{color:var(--muted)}}
.btn{{padding:0.6rem 1.2rem;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface-2);color:var(--text);font-family:var(--font);font-size:0.85rem;cursor:pointer;transition:all 0.2s}}
.btn:hover{{border-color:var(--primary);color:var(--primary)}}
.btn.active{{background:var(--primary-glow);border-color:var(--primary);color:var(--primary)}}
.export-btn{{background:var(--primary);border-color:var(--primary);color:#fff;font-weight:600}}
.export-btn:hover{{background:#5457e8;border-color:#5457e8;color:#fff}}

/* Legend */
.legend{{display:flex;gap:1.2rem;flex-wrap:wrap;margin-bottom:1.2rem;font-size:0.78rem;color:var(--muted)}}
.legend-dot{{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:4px;vertical-align:middle}}

/* Table */
.table-wrap{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden}}
table{{width:100%;border-collapse:collapse}}
thead{{background:var(--surface-2)}}
th{{padding:0.85rem 1rem;font-size:0.72rem;font-weight:600;text-transform:uppercase;letter-spacing:0.07em;color:var(--muted);text-align:left;cursor:pointer;user-select:none;white-space:nowrap}}
th:hover{{color:var(--text)}}
th.sorted{{color:var(--primary)}}
th .sort-icon{{margin-left:4px;opacity:0.5}}
th.sorted .sort-icon{{opacity:1}}
.row{{border-bottom:1px solid var(--faint);cursor:pointer;transition:background 0.15s}}
.row:last-child{{border-bottom:none}}
.row:hover{{background:rgba(99,102,241,0.06)}}
td{{padding:0.85rem 1rem;font-size:0.875rem;vertical-align:middle}}
.rank{{font-family:var(--mono);font-size:0.78rem;color:var(--muted);font-weight:600;width:40px}}
.keyword{{font-weight:500;font-family:var(--mono);font-size:0.82rem;color:var(--text)}}
.badge{{display:inline-flex;align-items:center;justify-content:center;width:46px;height:26px;border-radius:20px;font-size:0.78rem;font-weight:700;color:#fff}}

/* Toast */
.toast{{position:fixed;bottom:1.5rem;right:1.5rem;background:var(--surface-2);border:1px solid var(--primary);color:var(--text);padding:0.7rem 1.2rem;border-radius:var(--radius);font-size:0.85rem;opacity:0;transform:translateY(10px);transition:all 0.25s;pointer-events:none;z-index:999}}
.toast.show{{opacity:1;transform:translateY(0)}}

/* Empty */
.empty{{padding:4rem;text-align:center;color:var(--muted);font-size:0.9rem}}

/* Footer */
.footer{{margin-top:2rem;font-size:0.75rem;color:var(--faint);text-align:center}}

@media(max-width:600px){{
  body{{padding:1rem 0.75rem}}
  td,th{{padding:0.65rem 0.6rem;font-size:0.78rem}}
  .keyword{{font-size:0.74rem}}
}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="logo">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><path d="M11 8v6M8 11h6"/></svg>
      <span class="logo-text">KDP Niche Scanner</span>
    </div>
    <h1>Results for <span>"{topic}"</span></h1>
    <p class="meta">Generated {ts} &nbsp;·&nbsp; Filtered to &lt;{max_competitors:,} competitors &nbsp;·&nbsp; Click any row to copy keyword</p>
  </div>

  <div class="stats">
    <div class="stat"><div class="stat-label">Keywords Found</div><div class="stat-value purple" id="total-count">{total}</div></div>
    <div class="stat"><div class="stat-label">High Opportunity (70+)</div><div class="stat-value green" id="high-count">{sum(1 for r in results if r['opportunity_score'] >= 70)}</div></div>
    <div class="stat"><div class="stat-label">Medium (40–69)</div><div class="stat-value amber" id="med-count">{sum(1 for r in results if 40 <= r['opportunity_score'] < 70)}</div></div>
    <div class="stat"><div class="stat-label">Avg Score</div><div class="stat-value purple" id="avg-score">{round(sum(r['opportunity_score'] for r in results)/max(len(results),1),1)}</div></div>
  </div>

  <div class="controls">
    <input class="search-box" type="text" id="filterInput" placeholder="Filter keywords..." oninput="filterTable()">
    <button class="btn" id="btn-all" onclick="setFilter('all')" title="Show all">All</button>
    <button class="btn" id="btn-high" onclick="setFilter('high')" title="Score 70+">🟢 High</button>
    <button class="btn" id="btn-med" onclick="setFilter('medium')" title="Score 40-69">🟡 Medium</button>
    <button class="btn export-btn" onclick="exportCSV()">Export CSV</button>
  </div>

  <div class="legend">
    <span><span class="legend-dot" style="background:#22c55e"></span>Score 70+ = High opportunity</span>
    <span><span class="legend-dot" style="background:#f59e0b"></span>Score 40–69 = Medium</span>
    <span><span class="legend-dot" style="background:#ef4444"></span>Score &lt;40 = Competitive</span>
  </div>

  <div class="table-wrap">
    <table id="resultsTable">
      <thead>
        <tr>
          <th onclick="sortTable(0)">#<span class="sort-icon">↕</span></th>
          <th onclick="sortTable(1)">Keyword<span class="sort-icon">↕</span></th>
          <th onclick="sortTable(2)" class="sorted">Score<span class="sort-icon">↓</span></th>
          <th onclick="sortTable(3)">Competitors<span class="sort-icon">↕</span></th>
          <th onclick="sortTable(4)">Top BSR<span class="sort-icon">↕</span></th>
          <th onclick="sortTable(5)">Est. Daily Sales<span class="sort-icon">↕</span></th>
          <th onclick="sortTable(6)">AC Rank<span class="sort-icon">↕</span></th>
        </tr>
      </thead>
      <tbody id="tableBody">
        {rows_html}
      </tbody>
    </table>
    <div class="empty" id="emptyMsg" style="display:none">No keywords match your filter.</div>
  </div>

  <div class="footer">KDP Niche Scanner &nbsp;·&nbsp; Data sourced from Amazon autocomplete &amp; search results &nbsp;·&nbsp; Competition scores are estimates</div>
</div>

<div class="toast" id="toast"></div>

<script>
// Store original data
const RAW_DATA = {json.dumps(results)};
let currentFilter = 'all';
let sortCol = 2;
let sortAsc = false;

function setFilter(f) {{
  currentFilter = f;
  document.querySelectorAll('.controls .btn:not(.export-btn)').forEach(b => b.classList.remove('active'));
  document.getElementById('btn-' + (f === 'all' ? 'all' : f === 'high' ? 'high' : 'med')).classList.add('active');
  renderTable();
}}

function filterTable() {{ renderTable(); }}

function renderTable() {{
  const q = document.getElementById('filterInput').value.toLowerCase();
  const tbody = document.getElementById('tableBody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  let visible = 0;
  rows.forEach(row => {{
    const kw = row.querySelector('.keyword').textContent.toLowerCase();
    const score = parseFloat(row.querySelector('.badge').textContent);
    const matchQ = kw.includes(q);
    const matchF = currentFilter === 'all' ? true :
                   currentFilter === 'high' ? score >= 70 :
                   (score >= 40 && score < 70);
    row.style.display = (matchQ && matchF) ? '' : 'none';
    if (matchQ && matchF) visible++;
  }});
  document.getElementById('emptyMsg').style.display = visible === 0 ? 'block' : 'none';
}}

function sortTable(col) {{
  const th = document.querySelectorAll('th');
  th.forEach(t => t.classList.remove('sorted'));
  th[col].classList.add('sorted');
  if (sortCol === col) {{ sortAsc = !sortAsc; }} else {{ sortCol = col; sortAsc = col !== 2; }}
  th[col].querySelector('.sort-icon').textContent = sortAsc ? '↑' : '↓';
  const tbody = document.getElementById('tableBody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a, b) => {{
    const aVal = a.querySelectorAll('td')[col].textContent.replace(/[^0-9.-]/g,'') || a.querySelectorAll('td')[col].textContent;
    const bVal = b.querySelectorAll('td')[col].textContent.replace(/[^0-9.-]/g,'') || b.querySelectorAll('td')[col].textContent;
    const aNum = parseFloat(aVal), bNum = parseFloat(bVal);
    const cmp = isNaN(aNum) || isNaN(bNum) ? aVal.localeCompare(bVal) : aNum - bNum;
    return sortAsc ? cmp : -cmp;
  }});
  rows.forEach(r => tbody.appendChild(r));
  renderTable();
}}

function copyKw(row) {{
  const kw = row.querySelector('.keyword').textContent;
  navigator.clipboard.writeText(kw).catch(() => {{}});
  showToast('Copied: ' + kw);
}}

function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}}

function exportCSV() {{
  const headers = ['Rank','Keyword','Opportunity Score','Competitors','Top BSR','Est Daily Sales','AC Position'];
  const rows = RAW_DATA.map((r,i) => [
    i+1, r.keyword, r.opportunity_score, r.competitor_count,
    r.top_bsr || '', r.est_daily_sales, r.autocomplete_position
  ]);
  const csv = [headers, ...rows].map(r => r.map(v => `"${{v}}"`).join(',')).join('\n');
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
  a.download = 'kdp-niche-{topic.replace(" ","-")}.csv';
  a.click();
  showToast('CSV exported!');
}}

// Init
document.getElementById('btn-all').classList.add('active');
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="KDP Niche Scanner")
    parser.add_argument("--topic", required=True, help="Seed topic, e.g. 'fitness journal'")
    parser.add_argument("--max-competitors", type=int, default=50,
                        help="Max competitor count to include in results (default: 2000)")
    parser.add_argument("--output", default=None, help="Output HTML filename (default: auto-generated)")
    args = parser.parse_args()

    topic = args.topic.strip().lower()
    max_comp = args.max_competitors
    out_file = args.output or f"kdp-niche-{topic.replace(' ', '-')}.html"

    print(f"\n🔍 KDP Niche Scanner")
    print(f"   Topic       : {topic}")
    print(f"   Max comp.   : {max_comp:,}")
    print(f"   Output file : {out_file}\n")

    print("Stage 1/3 — Expanding keywords via Amazon autocomplete...")
    raw = get_suggestions(topic)
    deduped = dedupe_keywords(raw)
    print(f"  → Found {len(deduped)} unique keywords\n")

    if not deduped:
        print("No keywords found. Try a different seed topic.")
        return

    print("Stage 2/3 — Scoring competition for each keyword (this takes a few minutes)...")
    scored = []
    kw_list = list(deduped.values())
    for idx, item in enumerate(kw_list, 1):
        comp = get_competition(item["keyword"])
        item.update(comp)
        item["est_daily_sales"] = estimate_daily_sales(item.get("top_bsr"))
        item["opportunity_score"] = opportunity_score(
            item["autocomplete_position"], item["competitor_count"]
        )
        if item["competitor_count"] <= max_comp or item["competitor_count"] == 0:
            scored.append(item)
        print(f"  [{idx}/{len(kw_list)}] {item['keyword'][:55]:<55} "
              f"comp={item['competitor_count']:>5,}  score={item['opportunity_score']:>5}")

    print(f"\nStage 3/3 — Ranking {len(scored)} qualifying keywords...")
    scored.sort(key=lambda x: x["opportunity_score"], reverse=True)

    html = build_html(topic, scored, max_comp)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Done! Results saved to: {out_file}")
    print(f"   Total keywords : {len(scored)}")
    if scored:
        print(f"   Top keyword    : {scored[0]['keyword']} (score: {scored[0]['opportunity_score']})")
        print(f"   High opp (70+) : {sum(1 for r in scored if r['opportunity_score'] >= 70)}")


if __name__ == "__main__":
    main()
