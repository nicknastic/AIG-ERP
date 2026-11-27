# Hosting on Azure Static Web Apps (Free)

Everything runs in the **adriaticintelligence.com** Azure directory:

| Piece | What it is |
|---|---|
| Resource group | `aig-expenses-rg` (West US 2) in subscription *sub-ai-readiness* |
| Static Web App | `aig-expenses`, Free tier: <https://green-water-082a1fe1e.5.azurestaticapps.net> |
| Website | `frontend/`: plain HTML and JavaScript, served by the Static Web App |
| API | `api/`: Python Azure Functions (FastAPI), managed by the Static Web App |
| Records | The Excel workbook, `AIG Expenses/AIG Expenses.xlsx` by default |
| Receipt photos | `AIG Expenses/Receipts/<year>/…` by default |
| Settings | `AIG Expenses/settings.json` |

The workbook, photos and settings live in one Microsoft 365 document library
(the "AIG Expenses" SharePoint site, or a OneDrive). Nothing is stored in the
Static Web App itself, so the whole setup stays on the free tier. The only
running cost is reading receipts with Claude, roughly a cent or two each.

## Sign-in

Static Web Apps signs people in with Microsoft Entra ID (`/.auth/login/aad`).
Every page and API call requires a signed-in user (`frontend/staticwebapp.config.json`),
and the API additionally only accepts the emails in `ALLOWED_EMAILS`.

## App settings (Static Web App > Settings > Environment variables)

| Setting | Value |
|---|---|
| `ALLOWED_EMAILS` | `nick@adriaticintelligence.com,marija@adriaticintelligence.com` |
| `GRAPH_TENANT_ID` | The adriaticintelligence.com directory ID |
| `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` | The "AIG expenses" app registration (secret expires in 2 years) |
| `SHAREPOINT_SITE_ID` | The "AIG Expenses" SharePoint site (app has `Sites.Selected` on that site only) |
| `ONEDRIVE_USER` | Alternative to the site: a person's OneDrive (needs the broader `Files.ReadWrite.All`) |
| `ANTHROPIC_API_KEY` | Key for reading receipts. Without it, details are typed in by hand |

## Deploying

The workflow `.github/workflows/azure-static-web-app.yml` runs the tests and
deploys `frontend/` and `api/` on every push to `main`. It needs one GitHub
secret, `AZURE_STATIC_WEB_APPS_API_TOKEN`, the Static Web App's deployment token:

```powershell
az staticwebapp secrets list -n aig-expenses -g aig-expenses-rg --query properties.apiKey -o tsv
```

Add it under GitHub > Settings > Secrets and variables > Actions.

## Limits of the Free tier to know about

- API calls time out after 45 seconds. Reading one receipt takes a few seconds,
  and the site uploads several files one at a time, so this is fine.
- The API sleeps when unused; the first call after a quiet spell takes a few seconds.
- Two people saving at the very same moment is handled: the second save retries.
- If the workbook is open for editing in Excel at the moment a receipt is
  saved, OneDrive/SharePoint may refuse the write; the app says so and you try
  again after closing it.
