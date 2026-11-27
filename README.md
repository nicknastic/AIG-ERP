# AIG expenses

A small web app for collecting the business expenses of AIG, a general
partnership in British Columbia between Nick and Marija, and turning them into
the numbers each partner needs for their T2125 at tax time.

## What it does

- **Snap receipts from the iPhone browser**: open the site in Safari, tap
  *Take a photo of the receipt*, and the photo is saved and read straight away.
  No App Store app needed. Tip: in Safari use **Share > Add to Home Screen** so
  it opens like an app.
- **Receipt photos folder and Excel folder** are set in **Settings**. Photos are
  filed by year with descriptive names, for example
  `2026/2026-03-02 Cactus Club - Entertainment business development - 17.jpg`.
  An Excel workbook per year (`AIG Expenses 2026.xlsx`) is rewritten after every
  change, with an *Expenses* sheet and a *Summary* sheet split by partner.
- **Expense types**: each receipt is classified as Travel, Equipment,
  Entertainment / business development, Office expenses, Phone and internet,
  Vehicle, and so on. Each type maps to a CRA T2125 line.
- **Upload receipts**: phone photos (JPEG, PNG, HEIC) and PDFs, many at once.
- **Upload email receipts**: save the email as `.eml` (Outlook on the web: open
  the message, then **... > Download > Download as EML**) and upload it. Images
  and PDFs attached to the email are read too. Classic Outlook `.msg` files work
  if the optional `extract-msg` package is installed.
- **Reads each receipt with Claude**: vendor, date, subtotal, GST/HST, PST, tip,
  total, payment method, the supplier's GST number, and a suggested CRA
  category. Everything lands in a **Review** queue so you can check it against
  the photo before confirming.
- **CRA categories**: every expense goes to a T2125 line (8521 Advertising,
  8523 Meals and entertainment, 8810 Office expenses, 9200 Travel, 9281 Motor
  vehicle, 9936 CCA, and so on).
- **Tax rules applied**:
  - Meals and entertainment are limited to 50%.
  - The partnership is not GST-registered (the default), so GST and PST are
    part of each expense. If that changes, one switch in Settings splits GST
    out as input tax credits instead.
  - Business-use percentage per expense (for example a phone used 60% for work).
  - Foreign-currency receipts take an exchange rate.
  - Capital purchases (line 9936) are listed separately because they are
    claimed through CCA, not expensed in one year.
- **Partner split**: the deductible total for each line is split between the
  partners using the shares in Settings (50/50 by default).
- **Flags before filing**: unconfirmed expenses, missing dates, foreign amounts
  with no exchange rate, and GST receipts of $30 or more without the supplier's
  GST number (CRA needs it to allow the credit).
- **Exports**: a full ledger CSV, a T2125 summary CSV, and an accountant
  package (.zip) with both CSVs plus every receipt file for the year.
- **Duplicates**: uploading the same file twice is caught.

Receipts and Excel files go to the folders chosen in Settings: OneDrive when
hosted on Azure, or folders on the computer when run locally (default
`data/receipts` and `data/excel`). CRA
expects records to be kept for six years, so pick a folder that is backed up,
such as a OneDrive folder.

## Hosted on Azure

To use it from your phones anywhere, host it on Azure App Service with
Microsoft sign-in and OneDrive storage. Step by step: [deploy/AZURE.md](deploy/AZURE.md).

## Run it on your own computer

You need Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then put your Anthropic API key in .env
set -a; source .env; set +a      # Windows PowerShell: $env:ANTHROPIC_API_KEY="..."
uvicorn expenses.main:app --reload
```

Open <http://localhost:8000>.

### Using it from the iPhone

Start it so other devices on your Wi-Fi can reach it:

```bash
uvicorn expenses.main:app --host 0.0.0.0 --port 8000
```

Then on the iPhone open `http://<your-computer's-IP>:8000/snap` in Safari and
add it to the Home Screen. There is no login yet, so only do this on a home
network you trust.

### Automatic reading

Receipt reading uses the Anthropic API and needs an API key from
<https://console.anthropic.com>. Each receipt costs roughly a cent or two.
Without a key the app still works and you type the details in yourself.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Not tax advice

The categories and rules follow CRA's T2125 guide as of 2026, but check the
summary with your accountant before filing.
