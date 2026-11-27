# Hosting on Azure

The app runs on **Azure App Service** (Linux, Python), so Nick and Marija can
use it from their phones anywhere. It goes in the Azure subscription of the
**adriaticintelligence.com** directory. Sign-in is with your
adriaticintelligence.com work accounts (only that directory, and only the emails
in `ALLOWED_EMAILS`). Receipt photos and the Excel workbook are saved to a
folder in the connected OneDrive for Business.

Why App Service and not Static Web Apps: Static Web Apps serves static pages,
and its built-in API can't keep the database or files between requests. This
app needs a small always-running server, which is what App Service is for.

## Cost

- **B1** plan (default): about US$13 a month, always awake.
- **F1** plan: free, but it falls asleep when idle, so the first page after a
  break can take 20 to 30 seconds. Use `SKU=F1` in the setup command to try it.
- Reading receipts with Claude: roughly a cent or two each.

## One-time setup (about 10 minutes)

1. Sign in at <https://portal.azure.com> with your adriaticintelligence.com
   account. If you have several directories, check the top-right corner shows
   Adriatic Intelligence (account icon > Switch directory). Open **Cloud Shell**
   (the `>_` icon at the top) and choose **Bash**.
2. Upload `deploy/setup-azure.sh` (Cloud Shell toolbar > Manage files > Upload),
   then run, with your own app name and emails:

   ```bash
   APP_NAME=aig-expenses \
   ALLOWED_EMAILS="nick@adriaticintelligence.com,marija@adriaticintelligence.com" \
   bash setup-azure.sh
   ```

   It checks you're in the adriaticintelligence.com directory, asks for your
   Anthropic API key (for reading receipts), and if you have more than one
   subscription you can add `SUBSCRIPTION="<name>"` to pick it. `APP_NAME`
   becomes the address, so it must be unused across all of Azure.
3. Follow the last lines it prints: add the publish profile as the GitHub
   secret `AZURE_WEBAPP_PUBLISH_PROFILE` and the app name as the GitHub variable
   `AZURE_WEBAPP_NAME`.
4. In GitHub, open **Actions > Deploy to Azure > Run workflow**. Every push to
   `main` deploys from then on, after the tests pass.
5. Open `https://<APP_NAME>.azurewebsites.net`, sign in, open **Settings** and
   press **Connect OneDrive**. Pick the folders for receipts and the Excel file
   (defaults: `AIG Expenses/Receipts` and `AIG Expenses/Excel`).
6. On each iPhone: open `https://<APP_NAME>.azurewebsites.net/snap` in Safari,
   then **Share > Add to Home Screen**.

## Where things live

- **OneDrive** (the account that pressed Connect): receipt photos, filed by
  year, and one Excel workbook per year. Marija's receipts go to the same
  OneDrive. Share the folder with her in OneDrive if she wants to browse it.
- **App Service storage** (`/home/data`): the app's database (expense details,
  settings). It persists across restarts and deploys. The Excel workbook is a
  full copy of it, refreshed after every change.
- If OneDrive can't be reached when a receipt is snapped, the photo is kept on
  the server and moved to OneDrive the next time that expense is saved.

## Changing settings later

Portal > your web app > **Settings > Environment variables**:

| Setting | What it is |
|---|---|
| `ALLOWED_EMAILS` | Who can sign in, comma-separated |
| `ANTHROPIC_API_KEY` | Key for reading receipts |
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET` | The sign-in app registration (secret expires in 2 years) |
| `MS_TENANT` | The adriaticintelligence.com directory ID, so only its accounts can sign in |
| `SESSION_SECRET` | Signs the login cookie. Changing it signs everyone out |
| `PUBLIC_URL` | The site address, used for the sign-in redirect |
