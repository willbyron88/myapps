"""
wpp_trends.py — Google Trends intelligence for WPP content pillars.

Fetches 90-day trend data for each pillar keyword, computes a current
score and 30-day direction, then returns an HTML section for the CEO tab.

Results are cached for 23 hours so the dashboard doesn't hammer Google
on every run. Cache file: trends_cache.json
"""

import json
import time
import os
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = Path("trends_cache.json")
CACHE_HOURS = 23  # refresh once per day

# Pillar name → Google Trends search keyword
# Generic single-word pillars need context to get meaningful results
PILLAR_KEYWORDS = {
    "Sleep":      "sleep optimization",
    "Muscle":     "muscle after 50",
    "Bone":       "bone density after 50",
    "Cholesterol":"cholesterol management",
    "Cortisol":   "cortisol levels",
    "Hormones":   "hormone optimization",
    "Zone 2":     "Zone 2 training",
    "Mindset":    "mindset over 50",
    "Nutrition":  "nutrition after 50",
    "Recovery":   "workout recovery",
    "Training":   "strength training over 50",
    "Bloodwork":  "blood biomarkers",
    "Cognition":  "brain health after 50",
    "Joints":     "joint health",
    "Fueling":    "athletic fueling",
    "Sweat Rate": "sweat rate training",
    "Heat":       "heat training",
    "Financial":  "financial health",
    "System":     "health system protocol",
}

# Pillars that are WB-specific or content meta — skip for trends
SKIP_PILLARS = {"Artifact", "Character", "Hook", "Invitation", "Science",
                "Plans", "Full Book Overview", "Short-Form Overview"}


def _load_cache():
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        cutoff = (datetime.now() - timedelta(hours=CACHE_HOURS)).isoformat()
        if data.get("fetched_at", "") < cutoff:
            return {}
        return data
    except Exception:
        return {}


def _save_cache(data):
    data["fetched_at"] = datetime.now().isoformat()
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _fetch_trends(pillars_to_fetch):
    """
    Fetch Google Trends for all pillars in batches of 5 (API limit).
    Returns {pillar: {"score": int, "direction": str, "peak": int, "keyword": str}}
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        return {}

    results = {}
    keyword_to_pillar = {v: k for k, v in pillars_to_fetch.items()}

    # Batch into groups of 5 (pytrends limit)
    keywords = list(pillars_to_fetch.values())
    batches = [keywords[i:i+5] for i in range(0, len(keywords), 5)]

    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(15, 30), requests_args={"headers": session.headers})

    for i, batch in enumerate(batches):
        if i > 0:
            time.sleep(12)   # polite gap between batches — avoids 429
        try:
            pytrends.build_payload(batch, timeframe="today 3-m", geo="US")
            df = pytrends.interest_over_time()

            if df.empty:
                for kw in batch:
                    pillar = keyword_to_pillar.get(kw, kw)
                    results[pillar] = {"score": 0, "direction": "—", "peak": 0, "keyword": kw}
                continue

            # Drop isPartial column if present
            if "isPartial" in df.columns:
                df = df.drop(columns=["isPartial"])

            # Last full week average = current score
            # Previous 4-week average = baseline for direction
            for kw in batch:
                if kw not in df.columns:
                    continue
                pillar = keyword_to_pillar.get(kw, kw)
                series = df[kw].dropna()
                if series.empty:
                    results[pillar] = {"score": 0, "direction": "—", "peak": 0, "keyword": kw}
                    continue

                current  = int(series.iloc[-7:].mean())   # last 7 days
                baseline = int(series.iloc[-35:-7].mean()) # prior 4 weeks
                peak     = int(series.max())

                if baseline > 0:
                    pct_change = (current - baseline) / baseline * 100
                    if pct_change >= 15:
                        direction = "rising"
                    elif pct_change <= -15:
                        direction = "falling"
                    else:
                        direction = "flat"
                else:
                    direction = "—"

                results[pillar] = {
                    "score":     current,
                    "direction": direction,
                    "peak":      peak,
                    "keyword":   kw,
                }

            # Be polite to Google between batches
            if len(batches) > 1:
                time.sleep(2)

        except Exception as e:
            for kw in batch:
                pillar = keyword_to_pillar.get(kw, kw)
                results[pillar] = {"score": 0, "direction": "error", "peak": 0,
                                   "keyword": kw, "error": str(e)}

    return results


def fetch_trends_data():
    """
    Return cached or freshly fetched trend data for all pillars.
    Partial results are cached — on subsequent runs only missing/errored
    pillars are re-fetched. Always returns a dict — never raises.
    """
    cache = _load_cache()
    existing = cache.get("results", {})

    # Which pillars still need fetching? (missing or previously errored)
    all_pillars = {k: v for k, v in PILLAR_KEYWORDS.items() if k not in SKIP_PILLARS}
    to_fetch = {k: v for k, v in all_pillars.items()
                if k not in existing or existing[k].get("direction") == "error"}

    if not to_fetch:
        print(f"Trends: using cached data from {cache.get('fetched_at','?')[:16]}")
        return existing

    print(f"Trends: fetching {len(to_fetch)} pillars from Google Trends...")
    time.sleep(5)  # brief warmup before first request
    new_results = _fetch_trends(to_fetch)

    # Merge new into existing (good results only overwrite errors)
    merged = {**existing, **new_results}
    good  = sum(1 for d in merged.values() if d.get("direction") != "error")
    total = len(merged)
    _save_cache({"results": merged})
    print(f"Trends: {good}/{total} pillars with good data, cache saved.")
    return merged


def build_trends_html(trends_data, pillar_engagement):
    """
    Build the Pillar Trend Intelligence HTML section.

    trends_data     — {pillar: {score, direction, peak, keyword}}
    pillar_engagement — {pillar: avg_eng_pct} from live dashboard df
    """
    if not trends_data:
        return """
        <div class="scoreboard-card" style="margin-bottom:20px">
            <h2 style="margin-top:0;font-size:15px">Pillar Trend Intelligence</h2>
            <p style="color:#AAB4C0;font-size:12px">No trend data available. Run pip install pytrends and try again.</p>
        </div>"""

    # Build combined rows: merge trends with your engagement data
    rows = []
    for pillar, tdata in trends_data.items():
        score     = tdata.get("score", 0)
        direction = tdata.get("direction", "—")
        peak      = tdata.get("peak", 0)
        keyword   = tdata.get("keyword", pillar)
        your_eng  = pillar_engagement.get(pillar)

        # Determine action signal
        if direction == "rising" and your_eng and your_eng >= 10:
            signal       = "POST NOW"
            signal_color = "#5CFF7E"
            signal_bg    = "rgba(92,255,126,0.1)"
        elif direction == "rising" and score >= 40:
            signal       = "Test It"
            signal_color = "#C9A84C"
            signal_bg    = "rgba(201,168,76,0.08)"
        elif direction == "falling":
            signal       = "Deprioritize"
            signal_color = "#FF6B6B"
            signal_bg    = "transparent"
        elif your_eng and your_eng >= 10:
            signal       = "Your Audience Loves It"
            signal_color = "#C9A84C"
            signal_bg    = "rgba(201,168,76,0.08)"
        else:
            signal       = "Watch"
            signal_color = "#6B7A8D"
            signal_bg    = "transparent"

        rows.append({
            "pillar":    pillar,
            "keyword":   keyword,
            "score":     score,
            "direction": direction,
            "peak":      peak,
            "your_eng":  your_eng,
            "signal":    signal,
            "sig_color": signal_color,
            "sig_bg":    signal_bg,
        })

    # Sort: POST NOW first, then by trend score desc
    order = {"POST NOW": 0, "Test It": 1, "Your Audience Loves It": 2,
              "Watch": 3, "Deprioritize": 4}
    rows.sort(key=lambda r: (order.get(r["signal"], 9), -r["score"]))

    dir_icon  = {"rising": "&#8679; Rising", "falling": "&#8681; Falling",
                 "flat": "&#8680; Flat", "—": "—", "error": "rate limited"}
    dir_color = {"rising": "#5CFF7E", "falling": "#FF6B6B", "flat": "#AAB4C0",
                 "—": "#6B7A8D", "error": "#6B7A8D"}

    table_rows = ""
    for r in rows:
        icon  = dir_icon.get(r["direction"], "—")
        dclr  = dir_color.get(r["direction"], "#AAB4C0")
        eng_str = f"{r['your_eng']}%" if r["your_eng"] is not None else "—"
        eng_clr = "#C9A84C" if r["your_eng"] and r["your_eng"] >= 10 else "#AAB4C0"

        # Bar is always gold — shows volume only, not direction
        bar_pct = min(r["score"], 100)

        table_rows += f"""
        <tr style="background:{r['sig_bg']}">
            <td style="font-size:12px;font-weight:bold">{r['pillar']}</td>
            <td>
                <div style="display:flex;align-items:center;gap:6px">
                    <div style="flex:1;background:#1A2A3A;border-radius:3px;height:8px;overflow:hidden">
                        <div style="width:{bar_pct}%;height:100%;background:#C9A84C;border-radius:3px"></div>
                    </div>
                    <span style="font-size:11px;color:#D7DEE8;min-width:24px">{r['score']}</span>
                </div>
            </td>
            <td style="text-align:left">
                <span style="color:{dclr};font-size:11px;font-weight:bold">{icon}</span>
            </td>
            <td style="text-align:center;font-size:11px;color:#6B7A8D">{r['peak']}</td>
            <td style="text-align:center;font-size:12px;color:{eng_clr};font-weight:bold">{eng_str}</td>
            <td>
                <span style="color:{r['sig_color']};font-size:11px;font-weight:bold">{r['signal']}</span>
            </td>
        </tr>"""

    # Cache timestamp
    cache_note = ""
    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
        fetched = cache.get("fetched_at", "")[:16].replace("T", " ")
        cache_note = f"Last fetched: {fetched} &nbsp;&middot;&nbsp; Refreshes every 23 hours"
    except Exception:
        pass

    return f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">Pillar Trend Intelligence
            <span style="font-size:11px;color:#AAB4C0;font-weight:normal;margin-left:8px">Google Trends &middot; US &middot; 90 days</span>
        </h2>
        <p style="color:#AAB4C0;font-size:11px;margin:0 0 12px;line-height:1.6">
            <strong style="color:#D7DEE8">Score 0&ndash;100</strong> = how many people are searching this topic right now (higher = more demand).
            &nbsp;&middot;&nbsp;
            <strong style="color:#D7DEE8">Direction</strong> = is that demand growing or shrinking over the last 30 days.
            &nbsp;&middot;&nbsp;
            A high score with <span style="color:#FF6B6B">Falling</span> means lots of volume but cooling off.
            A low score with <span style="color:#5CFF7E">Rising</span> means momentum is building &mdash; get in early.
        </p>
        <table class="scoreboard-table" style="width:100%">
            <thead><tr>
                <th>Pillar</th>
                <th>Trend Score</th>
                <th style="text-align:center">Direction</th>
                <th style="text-align:center">90d Peak</th>
                <th style="text-align:center">Your Eng%</th>
                <th>Action</th>
            </tr></thead>
            <tbody>{table_rows}</tbody>
        </table>
        <p style="color:#6B7A8D;font-size:10px;margin:10px 0 0">{cache_note}</p>
    </div>"""
