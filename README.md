# AIG expenses

A small web app for collecting the business expenses of AIG, a general
partnership in British Columbia between Nick and Marija, and turning them into
the numbers each partner needs for their T2125 at tax time.

It is hosted on **Azure Static Web Apps (Free)** in the adriaticintelligence.com
directory. Open it in Safari on the iPhone, sign in with your work account, and
use **Share > Add to Home Screen** so it opens like an app.

## What it does

- **Snap receipts from the iPhone browser.** Tap *Take a photo of the receipt*;
  the photo is shrunk on the phone, saved, and read straight away. No App Store
  app needed.
- **Reads each receipt with Claude**: vendor, date, subtotal, GST, PST, tip,
  total, payment method, and the **expense type** (Travel, Equipment,
  Entertainment / business development, Office expenses, Phone and internet,
  Vehicle...). Each type maps to a CRA T2125 line. Everything waits in a review
  queue until someone confirms it.
- **The Excel workbook is the record.** Every expense is a row in
  `AIG Expenses.xlsx`, with a *Summary <year>* sheet per year split between the
  partners. Fixes made directly in Excel (vendor, date, amounts, CRA line,
  status, notes) are picked up by the app.
- **Folders set in Settings**: where receipt photos go (filed by year, named
  like `2026-03-02 Cactus Club - Entertainment business development - 17.jpg`)
  and where the workbook lives.
- **Email receipts**: upload saved `.eml` files (Outlook on the web: ... >
  Download > Download as EML) on the Expenses page; attachments are read too.
- **Tax rules**: meals and entertainment at 50%; not GST-registered, so GST and
  PST are part of each expense (one switch in Settings changes that);
  business-use percentage; exchange rates for foreign receipts; equipment
  listed separately for CCA.
- **Exports**: ledger CSV, T2125 summary CSV, the workbook, and an accountant
  package (.zip) with every receipt for the year.

## Layout

| Folder | What |
|---|---|
| `frontend/` | The website (HTML, CSS, JavaScript) and `staticwebapp.config.json` |
| `api/` | Python API run by Azure Functions: `function_app.py` and the `aig` package |
| `tests/` | `pytest` tests |
| `deploy/AZURE.md` | Azure resources, settings and deployment |

## Run it on your own computer

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=...        # optional, for automatic reading
cd api && python -m aig.devserver   # http://localhost:8000, files go to ./data
```

## Tests

```bash
pytest
```

## Not tax advice

The categories and rules follow CRA's T2125 guide as of 2026, but check the
summary with your accountant before filing.
