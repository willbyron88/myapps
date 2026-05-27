# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

Last updated: May 2026

Project Overview
Will Power Protocols social analytics dashboard. Pulls data from Instagram, YouTube, Facebook, and X, merges it with a SQLite content database, and generates docs/index.html and docs/social_analytics.csv.

Architecture — Read Before Touching Anything
social_dashboard.py          — main orchestrator only · no business logic here
wpp_facebook.py              — Facebook Reels/video API + HTML builder
wpp_instagram.py             — Instagram @willpowerprotocols API
wpp_instagram_wb.py          — Instagram @will.byron88 API
wpp_youtube.py               — YouTube Data + Analytics API + OAuth
wpp_kdp.py                   — KDP revenue data + HTML builder
wpp_x_importer.py            — X CSV importer · CLI + importable module
wpp_fb_image_analytics.py    — Facebook image posts (PM + TPL pages)
Module rules:
Each module is self-contained: own load_dotenv(), own helpers, own imports
socialdashboard.py only calls fetch functions and buildhtml — no API logic lives here
Never move business logic into social_dashboard.py
New platform = new wpp_.py module
Run python social_dashboard.py after every change to confirm clean output
A clean run prints row counts per platform — if any platform returns 0 rows, investigate before finishing

Database Rules — Critical
DB location: C:\Users\willb\myapps\social-analytics-dashboard\wpp.db
Never:
Drop or rename any table without explicit author instruction
Change column names on existing tables — referenced across multiple modules
Add NOT NULL columns without a DEFAULT value
Use AUTOINCREMENT on pmposts or tplposts — post_id must be explicit and match content sheet numbers
Always:
Use explicit postid values in pmposts and tpl_posts INSERTs
Check existing row count before inserting to avoid ID conflicts
Test schema changes against existing rows before committing
Use safeint() and safefloat() helpers — never assume column types
Key tables:
books           — book catalog · book_key is primary key (WPP-01 etc)
content         — WPP Shorts + VideoEpisodes
x_analytics     — X post metrics · matched from content.x_url
kdp_snapshots   — monthly KDP revenue
assets          — brand properties (pages, channels, websites)
pm_posts        — Prehistoric Memories FB image posts · explicit post_id
tpl_posts       — The Protocol Lab FB image posts · explicit post_id

Environment Variables — .env Structure
# Facebook — three separate pages, each with own token
FB_PAGE_ID_WPP=...
FB_PAGE_ACCESS_TOKEN_WPP=...
FB_PAGE_ID_PM=...
FB_PAGE_ACCESS_TOKEN_PM=...
FB_PAGE_ID_TPL=...
FB_PAGE_ACCESS_TOKEN_TPL=...

# Instagram — two accounts
IG_ACCESS_TOKEN_WPP=...
IG_USER_ID=...
IG_ACCESS_TOKEN_WB=...
IG_USER_ID_WB=...

# YouTube
YOUTUBE_API_KEY=...
YOUTUBE_CHANNEL_ID=...

# Meta app
FB_APP_ID=...
FB_APP_SECRET=...
Rules:
Never consolidate page tokens — each Facebook page requires its own token
Never rename an env var without grepping for the old name across all .py files first
Never print token values to console even in debug mode

Facebook API Rules
Always use bulk metric fetch with one-at-a-time fallback — see wpp_facebook.py pattern
post_impressions is NOT available on all page tokens — remove if you see (#100) errors
postimpressionsunique IS available — use for reach
bluereelsplay_count is the best top-line metric for Reels
Never pass period=lifetime parameter — causes (#100) errors
Image posts and Reel/video posts require different metric sets — never mix them
Facebook token expires every 60 days — check .env comments for next refresh date

Output Rules
Dashboard output lives in docs/ only — never write elsewhere
Always confirm docs/index.html and docs/social_analytics.csv saved successfully
Unicode characters (→ · ✓) cause cp1252 errors on Windows console — use ASCII in print statements

Naming Conventions
# Platform labels — exact strings, used in dashboard filters
"Instagram"        # @willpowerprotocols
"Instagram-WB"     # @will.byron88
"YouTube"
"Facebook"         # Will Power Protocols Reels
"Facebook-Images"  # PM + TPL image posts
"X"

# Asset keys
"WPP-FB-01"  "WPP-IG-01"  "WPP-YT-01"  "WPP-X-01"  "WPP-SITE-01"
"WB-FB-01"   "WB-IG-01"   "WB-X-01"
"TPL-FB-01"

# Book keys
"WPP-01" through "WPP-0N"   # Will Power Protocols books
"WB-01"                      # What the Blood Knows (Will Byron)

What NOT To Do
Do not add new Python dependencies without asking
Do not refactor working modules unless explicitly asked — surgical changes only
Do not combine modules — each platform stays in its own file
Do not change HTML styling in build_html() without being asked
Do not modify wpp.db schema without confirming it won't break existing modules
Do not create book-specific Python files — wppbuilddocx.py is the universal build script

Verification — After Every Change
Run python social_dashboard.py and confirm:
All platforms fetch without errors
All platforms return rows greater than zero
docs/index.html and docs/social_analytics.csv saved successfully
No unhandled exceptions
API warnings about unsupported metrics are acceptable. Zero rows from any platform is not acceptable — investigate before finishing.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
