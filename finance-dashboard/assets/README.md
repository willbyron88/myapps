# assets/ — How to Add Your Financial Files

Drop any of your account exports, position sheets, or manually-entered holdings here.
The program reads **every supported file** in this folder and merges them into one portfolio.

---

## Supported formats

| Format | Extension | Notes |
|--------|-----------|-------|
| Schwab account summary PDF | `.pdf` | Schwab-specific layout; downloaded from schwab.com |
| Excel spreadsheet | `.xlsx` `.xls` | All sheets are read and merged |
| CSV export | `.csv` | Standard comma-separated; first row must be headers |
| Plain text table | `.txt` | Must be formatted as CSV (comma-separated) |
| JSON | `.json` | See JSON format section below |
| Word document | `.docx` | Must contain a table; first row is the header |

Files that cannot be parsed are skipped with a warning — they won't crash the run.

---

## Required columns (minimum)

Every file (except Schwab PDFs, which are auto-parsed) must have at least these two:

| What it is | Recognized column names |
|------------|------------------------|
| **Ticker / Symbol** *(required)* | `Symbol`, `Ticker`, `Stock`, `CUSIP`, `ISIN`, `Security ID` |
| **Market Value** *(required)* | `Market Value`, `MarketValue`, `Current Value`, `Mkt Value`, `Value`, `Amount`, `Total Value`, `Fair Value`, `Balance` |

Optional columns (used for categorization and display):

| What it is | Recognized column names |
|------------|------------------------|
| Name / description | `Name`, `Investment`, `Description`, `Security`, `Holding`, `Asset Name`, `Security Name` |
| Account / category | `Account`, `Category`, `Type`, `Asset Class`, `Asset Type`, `Account Type`, `Acct Type` |

Column matching is **case-insensitive** and looks for partial matches too
(e.g., a column called `"Mkt Value (USD)"` will match as a value column).

Dollar signs, commas, and parentheses for negatives are all handled automatically:
`$1,234.56`, `1234.56`, `(500.00)` all parse correctly.

---

## JSON format

Two shapes are accepted:

**Shape 1 — plain list:**
```json
[
  { "Symbol": "AAPL", "Market Value": 5000.00, "Name": "Apple Inc" },
  { "Symbol": "NVDA", "Market Value": 8200.00, "Name": "NVIDIA" }
]
```

**Shape 2 — wrapper object:**
```json
{
  "positions": [
    { "Symbol": "AAPL", "Market Value": 5000.00 }
  ]
}
```
(Also accepts `"holdings"` as the wrapper key.)

---

## If your column names don't match

Create a file called `column_map.json` in this `assets/` folder.
Map the program's internal field names to your actual column headers:

```json
{
  "ticker":       "My Ticker Column",
  "value":        "Current Balance",
  "name":         "Security Description",
  "category":     "Acct"
}
```

Supported keys: `ticker`, `symbol`, `value`, `market value`, `name`, `category`, `account`.
You only need to include the fields that don't auto-detect correctly.

---

## Schwab PDF notes

The PDF parser reads Schwab's **Account Summary** page layout.
Download it from: schwab.com → Accounts → Summary → Print / Save as PDF.
It captures Market Value (not cost basis) for all sections: Equities, ETFs, Mutual Funds, Cash.

Other brokerage PDFs (Fidelity, Vanguard, etc.) are **not** auto-parsed.
For those, export a CSV or XLSX positions file instead and drop that in here.

---

## Files that are always ignored

- `README.md` (this file)
- `column_map.json`
- Files starting with `~$` (Excel temp files)
- Your quarterly review prompt (any file with "prompt", "quarterly", "rotation", or "review" in the name)

---

## Example: multi-account setup

```
assets/
  Account Summary _ Charles Schwab.pdf   ← brokerage account (auto-parsed)
  OTHERASSETS.xlsx                        ← manual entries: pension, crypto, startups
  fidelity_401k.csv                       ← 401k CSV export from fidelity.com
  vanguard_ira.xlsx                       ← IRA export from vanguard.com
  column_map.json                         ← only needed if column names don't match
```

Each file's rows are tagged with the source filename so you can see in the dashboard
which account each position came from.
