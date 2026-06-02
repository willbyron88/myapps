# Portfolio X-Ray — Personal Finance Dashboard

A local Python tool that reads your investment accounts, x-rays your mutual funds and ETFs
down to their underlying stock holdings, and generates a single self-contained HTML report
with two tabs:

- **Portfolio X-Ray** — what you actually own, aggregated across all accounts, with fund
  look-through, theme buckets, crypto exposure, and concentration tables
- **Rotation Review** — an AI-generated analysis comparing your portfolio to current
  institutional outlooks from Goldman Sachs, Morgan Stanley, BlackRock, JPMorgan, and others,
  built from your own quarterly review prompt

Nothing is uploaded to any server. Everything runs on your machine. The HTML output is a
single file you can open in any browser, share, or archive.

---

## What you need before starting

- Python 3.10 or newer — https://www.python.org/downloads/
- An OpenAI API key (optional but recommended for the AI analysis tab) — https://platform.openai.com/api-keys
- Your brokerage account export(s) — see the Assets section below

---

## Setup (one time)

**1. Unzip the file**

Unzip to any folder on your computer. Example: `C:\finance-dashboard` or `~/finance-dashboard`.

**2. Open a terminal in that folder**

- Windows: open the folder in Explorer, right-click in an empty area, choose
  "Open in Terminal" (or search for "cmd" / "PowerShell" in the Start menu, then `cd` to the folder)
- Mac/Linux: open Terminal, type `cd ` then drag the folder into the window and press Enter

**3. Install dependencies**

```
pip install -r requirements.txt
```

This installs everything the program needs (pandas, PyMuPDF, yfinance, openai, etc.).
You only need to do this once, or again after updating the zip.

**4. Add your OpenAI API key (optional)**

Open the `.env` file in a text editor and add your key:

```
OPENAI_API_KEY=sk-...your key here...
```

Without this, the Rotation Review tab still works but uses a built-in rules-based summary
instead of a live AI analysis with web search.

---

## Adding your accounts

Put your account files in the `assets/` folder. The program reads every file in that folder
and merges them into one portfolio automatically.

See **[assets/README.md](assets/README.md)** for the full guide, but the short version:

| What you have | What to do |
|---------------|-----------|
| Charles Schwab account | Download the Account Summary PDF from schwab.com and drop it in `assets/` |
| Fidelity, Vanguard, E*Trade, etc. | Export a CSV or XLSX positions file from the broker's website and drop it in `assets/` |
| 401k, pension, or other accounts | Add them manually to a spreadsheet — see the template below |
| Crypto (Coinbase, etc.) | Add manually to a spreadsheet |
| Real estate, private investments | Add manually to a spreadsheet |

### Manual entry spreadsheet

For anything that doesn't export neatly from a broker, create an Excel or CSV file
with at least these two columns:

| Symbol | Market Value |
|--------|-------------|
| AAPL | 5000.00 |
| BTC | 3200.00 |
| MyPension | 45000.00 |

Optional columns you can add: `Name`, `Account`, `Category`

Save it as anything you like (e.g., `OTHERASSETS.xlsx`) and drop it in `assets/`.

A Symbol can be a real ticker (AAPL, NVDA) or any label you want to use
(MyPension, RealEstate, StartupInvestment). Real tickers get fund look-through and
theme mapping automatically; custom labels are treated as direct holdings.

---

## Running the program

Open a terminal in the project folder and run:

```
python portfolio_xray_report.py
```

That's it. The program will:

1. Read every file in `assets/`
2. Look up fund holdings via yfinance (requires internet)
3. Call OpenAI for the AI analysis tab (requires API key in `.env`)
4. Write the report to `output/latest_portfolio_xray.html`

Open that file in any browser when it's done.

### Options

```
python portfolio_xray_report.py --no-openai
```
Skip the OpenAI call entirely (faster, no API cost, uses built-in analysis instead).

```
python portfolio_xray_report.py --positions "assets/myfile.csv"
```
Use a single specific file instead of reading all files in `assets/`.

```
python portfolio_xray_report.py --prompt-file "my_review_prompt.docx"
```
Use a specific prompt file for the AI analysis tab.

```
python portfolio_xray_report.py --no-history
```
Skip saving a JSON snapshot to the `history/` folder.

---

## The quarterly review prompt

The AI analysis tab is guided by a prompt file you write — it tells the AI what questions
to answer, what guardrails to apply, and how to frame the analysis for your situation.

Name the file anything that includes the words `prompt`, `quarterly`, `rotation`, or `review`
and place it in the project folder (not inside `assets/`). Supported formats: `.docx` or `.txt`.

Example: `quarterly_review_prompt.docx`

The prompt can include things like:
- Your investment goals and time horizon
- Guardrails (e.g., "keep crypto under 15%", "don't add to speculative positions over 2%")
- Specific questions you want answered each quarter
- Rebalancing preferences (e.g., "use new contributions before trimming existing winners")

If no prompt file is found, the AI still runs but uses a generic framework.

---

## Output files

| File | What it is |
|------|-----------|
| `output/latest_portfolio_xray.html` | The report — open this in a browser |
| `output/debug/` | Raw OpenAI request and response files for troubleshooting |
| `history/portfolio_xray_TIMESTAMP.json` | Snapshot saved each run for trend tracking |

---

## Folder structure

```
finance-dashboard/
  portfolio_xray_report.py   <- the program
  requirements.txt           <- Python dependencies
  .env                       <- your API keys (never share this)
  README.md                  <- this file
  assets/
    README.md                <- guide to adding account files
    column_map.json          <- optional: map custom column names
    [your account files]     <- PDF, XLSX, CSV, JSON, DOCX, TXT
  output/
    latest_portfolio_xray.html
    debug/
  history/
```

---

## Troubleshooting

**"No positions found"**
Make sure your files are in the `assets/` folder and use a supported format.
Check that they have at least a ticker/symbol column and a market value column.
See `assets/README.md` for column name requirements.

**"Could not identify required columns"**
Your column names don't match any of the known aliases. Create a `column_map.json`
in `assets/` to tell the program which column is which. See `assets/README.md`.

**Fund look-through shows no holdings**
yfinance doesn't always have holdings data for every fund, especially smaller mutual funds.
You can supply your own via a `holdings_cache.csv` file in the project folder.
Columns: `fund_ticker, holding_ticker, holding_name, weight_pct`

**OpenAI analysis tab shows "local rules-based analysis"**
Either `OPENAI_API_KEY` is missing from `.env`, or the API call failed.
Check `output/debug/openai_error.txt` if it exists.

**The PDF didn't parse correctly**
The PDF parser is built specifically for the Charles Schwab Account Summary format.
For other brokerages, use their CSV or XLSX export instead.

---

## Privacy note

Your account data never leaves your computer unless you have an OpenAI API key configured,
in which case your portfolio summary (tickers and values, not account numbers) is sent to
OpenAI's API for analysis. No data is stored on any server by this program.
Never share your `.env` file — it contains your API key.
