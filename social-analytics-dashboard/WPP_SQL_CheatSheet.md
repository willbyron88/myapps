# WPP DATABASE — SQL CHEAT SHEET
Database: wpp.db · Engine: SQLite · Front end: DB Browser for SQLite
Download DB Browser: sqlitebrowser.org (free, Windows)

---

## TABLES
books                — master book registry (8 books)
content              — every Short, episode, blog post, X content
analytics_snapshots  — historical performance per post (populated by dashboard script)
kdp_snapshots        — monthly KDP revenue per book (manual entry)
x_analytics          — weekly X metrics (manual entry)

---

## DAILY OPERATIONS

### Mark a Short as posted
```sql
UPDATE content
SET posted = 'Y',
    instagram_url = 'https://www.instagram.com/reel/XXXXX/',
    youtube_url   = 'https://www.youtube.com/watch?v=XXXXX',
    x_url         = 'https://x.com/wpprotocols/status/XXXXX',
    post_date     = date('now')
WHERE book_key   = 'WPP-01'
AND   content_type = 'Short'
AND   short_num  = 4;
```

### Mark a podcast/video episode as posted
```sql
UPDATE content
SET posted      = 'Y',
    youtube_url = 'https://www.youtube.com/watch?v=XXXXX',
    x_url       = 'https://x.com/wpprotocols/status/XXXXX',
    post_date   = date('now')
WHERE book_key    = 'WPP-01'
AND   content_type = 'Video+Podcast'
AND   episode_num  = 1;
```

### Add a new book
```sql
INSERT INTO books
(book_key, title, subtitle, series, series_position, brand,
 kindle_asin, paperback_asin, kindle_price, paperback_price,
 kdp_live_date, status)
VALUES
('WPP-08', 'The Muscle After 50 Protocol',
 'A Science-Backed Strength, Protein, and Recovery Plan',
 'Will Power Protocols — Longevity Series', 'Book 8',
 'WPP', NULL, NULL, 9.99, 19.99, NULL, 'Planned');
```

### Add Shorts for a new book (run once per book, 5 rows)
```sql
INSERT INTO content
(book_key, content_type, short_num, content_pillar, campaign, script_topic, ig_account, x_account, posted)
VALUES
('WPP-08', 'Short', 1, 'Muscle',   'Muscle After 50 Launch', 'Short 1 topic here', '@willpowerprotocols', '@wpprotocols', 'N'),
('WPP-08', 'Short', 2, 'Protein',  'Muscle After 50 Launch', 'Short 2 topic here', '@willpowerprotocols', '@wpprotocols', 'N'),
('WPP-08', 'Short', 3, 'Recovery', 'Muscle After 50 Launch', 'Short 3 topic here', '@willpowerprotocols', '@wpprotocols', 'N'),
('WPP-08', 'Short', 4, 'Strength', 'Muscle After 50 Launch', 'Short 4 topic here', '@willpowerprotocols', '@wpprotocols', 'N'),
('WPP-08', 'Short', 5, 'System',   'Muscle After 50 Launch', 'Short 5 topic here', '@willpowerprotocols', '@wpprotocols', 'N');
```

### Update KDP revenue (monthly — enter after KDP report downloads)
```sql
INSERT INTO kdp_snapshots
(snapshot_month, book_key, kindle_units, paperback_units, ku_pages, royalties_usd)
VALUES
('2026-05', 'WPP-01', 4, 0, 5, 18.00),
('2026-05', 'WPP-02', 0, 0, 0, 0.00),
('2026-05', 'WPP-03', 0, 0, 0, 0.00),
('2026-05', 'WPP-04', 0, 0, 0, 0.00),
('2026-05', 'WPP-05', 0, 0, 0, 0.00),
('2026-05', 'WPP-06', 1, 0, 0, 5.56),
('2026-05', 'WB-01',  0, 0, 5, 0.02);
```

### Update X analytics (weekly — from X Premium export)
```sql
INSERT INTO x_analytics
(snapshot_date, x_url, book_key, content_pillar, impressions, likes, reposts, profile_visits, link_clicks)
VALUES
(date('now'), 'https://x.com/wpprotocols/status/XXXXX', 'WPP-01', 'Sleep', 142, 3, 1, 8, 2);
```

---

## MONDAY MORNING QUERIES

### What's still in the queue this week?
```sql
SELECT b.book_key, c.content_type, c.short_num, c.episode_num,
       c.content_pillar, c.script_topic
FROM content c
JOIN books b ON c.book_key = b.book_key
WHERE c.posted = 'N'
AND   c.content_type = 'Short'
ORDER BY b.book_key, c.short_num;
```

### Book scoreboard — views and engagement
```sql
SELECT b.book_key, b.title,
       COUNT(DISTINCT a.post_url) AS posts,
       SUM(a.views) AS total_views,
       ROUND(AVG(a.engagement_pct), 2) AS avg_engagement
FROM analytics_snapshots a
JOIN content c ON a.post_url = c.youtube_url OR a.post_url = c.instagram_url
JOIN books b ON c.book_key = b.book_key
WHERE a.snapshot_date = (SELECT MAX(snapshot_date) FROM analytics_snapshots)
GROUP BY b.book_key
ORDER BY total_views DESC;
```

### Pillar scoreboard — which topic wins
```sql
SELECT c.content_pillar,
       COUNT(DISTINCT a.post_url) AS posts,
       SUM(a.views) AS total_views,
       ROUND(AVG(a.engagement_pct), 2) AS avg_engagement
FROM analytics_snapshots a
JOIN content c ON a.post_url = c.youtube_url OR a.post_url = c.instagram_url
WHERE a.snapshot_date = (SELECT MAX(snapshot_date) FROM analytics_snapshots)
GROUP BY c.content_pillar
ORDER BY total_views DESC;
```

### Revenue by book — all time
```sql
SELECT b.title, b.book_key,
       SUM(k.kindle_units)    AS total_kindle,
       SUM(k.paperback_units) AS total_paperback,
       SUM(k.ku_pages)        AS total_ku_pages,
       ROUND(SUM(k.royalties_usd), 2) AS total_revenue
FROM kdp_snapshots k
JOIN books b ON k.book_key = b.book_key
GROUP BY b.book_key
ORDER BY total_revenue DESC;
```

### Revenue trend — month by month
```sql
SELECT k.snapshot_month,
       SUM(k.royalties_usd) AS monthly_revenue,
       SUM(k.kindle_units)  AS kindle_units,
       SUM(k.ku_pages)      AS ku_pages
FROM kdp_snapshots k
GROUP BY k.snapshot_month
ORDER BY k.snapshot_month;
```

### Content performance trend — is Sleep growing?
```sql
SELECT a.snapshot_date, c.content_pillar,
       SUM(a.views) AS views,
       ROUND(AVG(a.engagement_pct), 2) AS avg_eng
FROM analytics_snapshots a
JOIN content c ON a.post_url = c.youtube_url OR a.post_url = c.instagram_url
WHERE c.content_pillar = 'Sleep'
GROUP BY a.snapshot_date
ORDER BY a.snapshot_date;
```

### Which Shorts are winners across all books
```sql
SELECT b.book_key, c.short_num, c.content_pillar, c.script_topic,
       MAX(a.views) AS peak_views,
       ROUND(MAX(a.engagement_pct), 2) AS peak_engagement,
       a.platform
FROM analytics_snapshots a
JOIN content c ON a.post_url = c.youtube_url OR a.post_url = c.instagram_url
JOIN books b ON c.book_key = b.book_key
WHERE a.content_signal LIKE '%Winner%'
GROUP BY c.content_id
ORDER BY peak_views DESC;
```

### What does X analytics look like vs Instagram/YouTube
```sql
-- X performance
SELECT x.snapshot_date, b.book_key, x.content_pillar,
       x.impressions, x.likes, x.reposts, x.profile_visits, x.link_clicks
FROM x_analytics x
JOIN books b ON x.book_key = b.book_key
ORDER BY x.snapshot_date DESC, x.impressions DESC;
```

---

## BOOK KEYS REFERENCE
WPP-01  Win the Long War (Men's)
WPP-02  Win the Long War Women's Edition
WPP-03  The Clydesdale Protocol
WPP-04  The Athena Protocol
WPP-05  The Roman Protocol
WPP-06  AI After 50
WPP-07  Testosterone After 50 (Planned)
WB-01   What the Blood Knows (Will Byron)

---

## DB BROWSER FOR SQLITE — QUICK REFERENCE
Open wpp.db → Browse Data tab to see rows like a spreadsheet
Execute SQL tab → paste any query above → F5 or click Execute
Export → File → Export → Table to CSV (if you ever need a CSV back)
Import → File → Import → Table from CSV

---

WPP Database · SQLite · May 2026
