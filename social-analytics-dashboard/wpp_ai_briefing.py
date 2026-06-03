"""
wpp_ai_briefing.py — AI-powered CEO briefing via OpenAI API.

Assembles a compact snapshot of Will's current social + KDP state,
sends it to GPT-4o Mini, and returns a styled HTML block for the CEO tab.

Skips gracefully if OPENAI_API_KEY is not set.
"""

import os
import sqlite3
from datetime import date
from dotenv import load_dotenv

load_dotenv()

WPP_DB_FILE = "wpp.db"


def _safe_int(v):
    try:
        return int(float(v or 0))
    except Exception:
        return 0


def _safe_float(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _build_data_snapshot(df):
    """Assemble a compact plain-text snapshot from live df + wpp.db."""
    lines = []
    today = date.today().strftime("%Y-%m-%d")
    lines.append(f"Date: {today}")
    lines.append("")

    # ── Platform summary ──────────────────────────────────────────
    lines.append("== PLATFORM PERFORMANCE (all-time in dashboard window) ==")
    platforms = df["platform"].unique() if "platform" in df.columns else []
    for plat in sorted(platforms):
        p = df[df["platform"] == plat]
        views = _safe_int(p["views"].sum())
        posts = len(p)
        avg_eng = round(float(p["engagement_rate_percent"].mean()), 1) if posts else 0
        lines.append(f"  {plat}: {posts} posts, {views:,} views, {avg_eng}% avg engagement")

    lines.append("")

    # ── Pillar performance ────────────────────────────────────────
    lines.append("== CONTENT PILLAR PERFORMANCE ==")
    if "content_pillar" in df.columns:
        known_p = df[df["content_pillar"].notna() & df["content_pillar"].ne("") & df["content_pillar"].ne("—")]
        if not known_p.empty:
            pillar_agg = (
                known_p.groupby("content_pillar")
                .agg(posts=("views", "count"), views=("views", "sum"), eng=("engagement_rate_percent", "mean"))
                .reset_index()
                .sort_values("eng", ascending=False)
            )
            for _, row in pillar_agg.iterrows():
                lines.append(
                    f"  {row['content_pillar']}: {int(row['posts'])} posts, "
                    f"{int(row['views']):,} views, {round(float(row['eng']), 1)}% avg engagement"
                )
        else:
            lines.append("  No pillar data matched yet.")
    lines.append("")

    # ── Book performance ──────────────────────────────────────────
    lines.append("== BOOK PERFORMANCE (views + winners) ==")
    if "book_or_offer" in df.columns and "content_signal" in df.columns:
        known_b = df[df["book_or_offer"].notna() & df["book_or_offer"].ne("—")]
        if not known_b.empty:
            book_agg = (
                known_b.groupby("book_or_offer")
                .agg(posts=("views", "count"), views=("views", "sum"), eng=("engagement_rate_percent", "mean"))
                .reset_index()
                .sort_values("views", ascending=False)
            )
            winner_counts = (
                known_b[known_b["content_signal"].str.contains("Winner", na=False)]
                .groupby("book_or_offer").size().to_dict()
            )
            for _, row in book_agg.iterrows():
                bk = row["book_or_offer"]
                winners = winner_counts.get(bk, 0)
                lines.append(
                    f"  {bk}: {int(row['posts'])} posts, "
                    f"{int(row['views']):,} views, {round(float(row['eng']), 1)}% avg engagement, "
                    f"{winners} winner posts"
                )
        else:
            lines.append("  No book data matched yet.")
    lines.append("")

    # ── KDP revenue ───────────────────────────────────────────────
    lines.append("== KDP REVENUE (last 2 months by book) ==")
    try:
        conn = sqlite3.connect(WPP_DB_FILE)
        cur = conn.cursor()
        cur.execute("""
            SELECT k.book_key, b.title, k.snapshot_year, k.snapshot_month,
                   k.royalties_usd, k.kindle_units, k.ku_pages
            FROM kdp_snapshots k JOIN books b ON k.book_key = b.book_key
            ORDER BY k.snapshot_year DESC, k.snapshot_month DESC
        """)
        kdp_rows = cur.fetchall()

        # Last 2 distinct months
        months_seen = []
        for r in kdp_rows:
            mo = (r[2], r[3])
            if mo not in months_seen:
                months_seen.append(mo)
            if len(months_seen) >= 2:
                break

        for bk, title, yr, mo, rev, kindle, ku in kdp_rows:
            if (yr, mo) in months_seen:
                lines.append(
                    f"  {title} ({mo} {yr}): ${_safe_float(rev):.2f} revenue, "
                    f"{_safe_int(kindle)} Kindle sales, {_safe_int(ku)} KU pages"
                )

        # Total and run rate
        cur.execute("""
            SELECT SUM(royalties_usd) FROM kdp_snapshots
            WHERE snapshot_year = (SELECT MAX(snapshot_year) FROM kdp_snapshots)
              AND snapshot_month = (SELECT MAX(snapshot_month) FROM kdp_snapshots
                                    WHERE snapshot_year = (SELECT MAX(snapshot_year) FROM kdp_snapshots))
        """)
        cur_month_rev = _safe_float((cur.fetchone() or [0])[0])
        days_elapsed = date.today().day
        run_rate = cur_month_rev / days_elapsed * 30 if days_elapsed > 0 else 0
        lines.append(f"  Current month run rate: ${run_rate:.2f}/mo (goal: $500/mo)")
        lines.append(f"  Progress to $500 goal: {min(run_rate / 500 * 100, 100):.1f}%")

        # ── Queue counts ──────────────────────────────────────────
        lines.append("")
        lines.append("== CONTENT QUEUE (unposted) ==")
        cur.execute("""
            SELECT b.title, COUNT(*) FROM content c JOIN books b ON c.book_key = b.book_key
            WHERE c.posted='N' AND c.content_type='Short'
            GROUP BY c.book_key ORDER BY b.title
        """)
        for title, cnt in cur.fetchall():
            lines.append(f"  {title}: {cnt} shorts ready to post")

        cur.execute("SELECT COUNT(*) FROM tpl_posts WHERE posted='N'")
        tpl_count = (cur.fetchone() or [0])[0]
        cur.execute("SELECT COUNT(*) FROM pm_posts WHERE posted='N'")
        pm_count = (cur.fetchone() or [0])[0]
        lines.append(f"  The Protocol Lab: {tpl_count} image posts unposted")
        lines.append(f"  Prehistoric Memories: {pm_count} image posts unposted")

        # ── Books with zero revenue ───────────────────────────────
        lines.append("")
        lines.append("== BOOKS WITH ZERO REVENUE (but content posted) ==")
        cur.execute("""
            SELECT b.title FROM books b
            WHERE b.status='Live'
              AND b.book_key NOT IN (
                  SELECT book_key FROM kdp_snapshots WHERE royalties_usd > 0
              )
        """)
        zero_rev_books = [r[0] for r in cur.fetchall()]
        if zero_rev_books:
            for t in zero_rev_books:
                lines.append(f"  {t}")
        else:
            lines.append("  All live books have earned at least some revenue.")

        conn.close()
    except Exception as e:
        lines.append(f"  (DB error: {e})")

    return "\n".join(lines)


def _build_whats_working(df, plan):
    """Build the same What's Working bullet text that the dashboard displays."""
    bullets = []
    if not plan:
        return ""

    if plan.get("has_content_map"):
        known = df[df["book_or_offer"].ne("—")] if "book_or_offer" in df.columns else df
        if not known.empty:
            book_agg = (
                known.groupby("book_or_offer")
                .agg(
                    views=("views", "sum"),
                    eng=("engagement_rate_percent", "mean"),
                    winners=("content_signal", lambda x: x.str.contains("Winner", na=False).sum()),
                )
                .reset_index()
            )
            top_vol = book_agg.sort_values("views", ascending=False).iloc[0]
            top_eng = book_agg.sort_values("eng",   ascending=False).iloc[0]
            bullets.append(
                f"{top_vol['book_or_offer']} leads by volume: "
                f"{_safe_int(top_vol['views']):,} views, {_safe_int(top_vol['winners'])} winner posts"
            )
            if top_eng["book_or_offer"] != top_vol["book_or_offer"]:
                bullets.append(
                    f"{top_eng['book_or_offer']} leads by engagement: "
                    f"{round(float(top_eng['eng']), 1)}% average"
                )

    if "content_pillar" in df.columns:
        known_p = df[df["content_pillar"].ne("—")] if "content_pillar" in df.columns else df
        if not known_p.empty:
            p_agg = (
                known_p.groupby("content_pillar")
                .agg(posts=("views", "count"), views=("views", "sum"), eng=("engagement_rate_percent", "mean"))
                .reset_index()
            )
            top_p = p_agg.sort_values("views", ascending=False).iloc[0]
            bullets.append(
                f"Top pillar by views: {top_p['content_pillar']} ({_safe_int(top_p['views']):,} views)"
            )
            gems = p_agg[(p_agg["posts"] <= 5) & (p_agg["eng"] >= 8.0)].sort_values("eng", ascending=False)
            if not gems.empty:
                g = gems.iloc[0]
                bullets.append(
                    f"{g['content_pillar']} has {round(float(g['eng']), 1)}% engagement "
                    f"on {_safe_int(g['posts'])} posts — high signal, low volume"
                )

    return "\n".join(f"- {b}" for b in bullets)


def generate_ai_briefing(df, plan=None, intel_signals=None):
    """
    Call OpenAI API with a data snapshot and return a styled HTML block.
    Returns a notice block if OPENAI_API_KEY is not set.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return """
        <div style="background:#1A2A3A;border:1px solid #2A3A4A;border-radius:10px;
                    padding:16px 20px;margin-bottom:24px;color:#AAB4C0;font-size:13px">
            <strong style="color:#C9A84C">AI Briefing</strong> &mdash;
            Add <code>OPENAI_API_KEY</code> to your .env to enable the AI briefing.
        </div>"""

    try:
        from openai import OpenAI
    except ImportError:
        return """
        <div style="background:#1A2A3A;border:1px solid #2A3A4A;border-radius:10px;
                    padding:16px 20px;margin-bottom:24px;color:#AAB4C0;font-size:13px">
            <strong style="color:#C9A84C">AI Briefing</strong> &mdash;
            Run <code>pip install openai</code> to enable the AI briefing.
        </div>"""

    snapshot      = _build_data_snapshot(df)
    whats_working = _build_whats_working(df, plan)

    # Format pre-computed intelligence signals if provided
    signals_block = ""
    if intel_signals:
        sig_lines = ["PRE-COMPUTED INTELLIGENCE SIGNALS (cross-referencing Shorts, Google Trends, KDP, Gumroad):"]
        all_sigs = (
            intel_signals.get("write_next", []) +
            intel_signals.get("post_priority", []) +
            intel_signals.get("gumroad_next", []) +
            intel_signals.get("revenue_alerts", []) +
            intel_signals.get("re_engage", [])
        )
        for sig in all_sigs:
            ev_str = " | ".join(f"{t}: {v}" for t, v, _ in sig.get("evidence", []))
            sig_lines.append(
                f"  [{sig['label']} — {sig.get('confidence','')}] {sig['title']}"
                + (f" — Evidence: {ev_str}" if ev_str else "")
            )
        if len(sig_lines) == 1:
            sig_lines.append("  No strong cross-validated signals yet.")
        signals_block = "\n".join(sig_lines)

    system_msg = (
        "You are Will's personal strategist for the Will Power Protocols self-publishing business. "
        "He writes health/longevity books for people over 50 and promotes them via Shorts on "
        "Instagram, YouTube, Facebook, and X.\n\n"
        "The dashboard already surfaces these What's Working facts — do NOT repeat them verbatim:\n"
        + (whats_working if whats_working else "(no pre-computed insights available)")
    )

    user_msg = f"""Here is the full performance data snapshot:

{snapshot}

{signals_block}

Give exactly 5 numbered action-oriented bullets. Each bullet must be 1-2 sentences max.
Be specific — use actual numbers from the data. The pre-computed signals above combine Shorts \
engagement, Google Trends, KDP revenue, and Gumroad data — reference them where relevant \
but go deeper or add nuance the signals don't capture.

1. SITUATION: One-sentence summary of where things stand right now.
2. TOP PRIORITY: The single most important action this week — must reference specific data.
3. MONETIZATION: Best revenue opportunity visible right now — be specific about which book/pillar/product.
4. GAP: The biggest weakness or blind spot in the current strategy.
5. HIDDEN INSIGHT: One non-obvious pattern the data reveals — something he probably hasn't noticed.

Respond with only the 5 numbered bullets. No intro, no outro."""

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=500,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
        )
        raw = response.choices[0].message.content.strip()
    except Exception as e:
        return f"""
        <div style="background:#1A2A3A;border:1px solid #FF6B6B;border-radius:10px;
                    padding:16px 20px;margin-bottom:24px;color:#FF6B6B;font-size:13px">
            <strong>AI Briefing error:</strong> {e}
        </div>"""

    # Parse bullets into HTML
    bullet_html = ""
    label_colors = {
        "1": ("#AAB4C0", "SITUATION"),
        "2": ("#C9A84C", "TOP PRIORITY"),
        "3": ("#5CFF7E", "MONETIZATION"),
        "4": ("#FF6B6B", "GAP"),
        "5": ("#E1A1FF", "HIDDEN INSIGHT"),
    }
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        num = line[0] if line and line[0].isdigit() else None
        if num and num in label_colors:
            color, label = label_colors[num]
            # Strip "1. SITUATION: " prefix variations
            text = line[2:].strip()
            # Remove label prefix if Claude included it
            for prefix in [label + ":", label.title() + ":", label.lower() + ":"]:
                if text.upper().startswith(label):
                    text = text[len(label):].lstrip(": ").strip()
                    break
            bullet_html += f"""
            <div style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.06)">
                <div style="min-width:130px;font-size:11px;font-weight:bold;
                            color:{color};padding-top:1px;letter-spacing:.5px">{label}</div>
                <div style="font-size:13px;color:#D7DEE8;line-height:1.5">{text}</div>
            </div>"""
        else:
            # Continuation line — append to last bullet (edge case)
            bullet_html += f'<div style="font-size:12px;color:#AAB4C0;padding:2px 0 2px 142px">{line}</div>'

    timestamp = date.today().strftime("%B %d, %Y")

    return f"""
    <div style="background:linear-gradient(135deg,#0D1F2D 0%,#132030 100%);
                border:1px solid #C9A84C;border-radius:10px;
                padding:20px 24px;margin-bottom:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
            <div style="font-family:'Bebas Neue','Impact',sans-serif;font-size:20px;
                        color:#C9A84C;letter-spacing:3px">AI BRIEFING</div>
            <div style="font-size:11px;color:#6B7A8D">Generated {timestamp} &nbsp;&middot;&nbsp; GPT-4o Mini</div>
        </div>
        {bullet_html}
    </div>"""
