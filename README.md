# AIG expenses

A small web app for collecting the business expenses of AIG, a general
partnership in British Columbia between Nick and Marija, and turning them into
the numbers each partner needs for their T2125 at tax time.

## What it does

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
  - If the partnership is GST-registered, GST/HST is taken out of the expense
    and totalled separately as input tax credits for the GST/HST return.
    BC PST is not recoverable, so it stays in the expense.
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

Receipts and the database stay on your computer in the `data/` folder. CRA
expects records to be kept for six years, so back that folder up.

## Run it

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

To upload straight from your phone on the same Wi-Fi, start it with
`uvicorn expenses.main:app --host 0.0.0.0` and open `http://<your-computer's-IP>:8000`
on the phone. There is no login yet, so only do this on a home network you trust.

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
