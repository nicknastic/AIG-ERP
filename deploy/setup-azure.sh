#!/usr/bin/env bash
# One-time Azure setup for the AIG expenses app.
#
# Run it in Azure Cloud Shell (portal.azure.com > the >_ icon > Bash):
#   curl -fsSLO https://raw.githubusercontent.com/nicknastic/AIG-ERP/main/deploy/setup-azure.sh   # or upload this file
#   APP_NAME=aig-expenses-nick ALLOWED_EMAILS="nick@outlook.com,marija@example.com" bash setup-azure.sh
#
# It creates: a resource group, an App Service plan and web app (Linux, Python),
# and a Microsoft sign-in app registration. It prints the two values to add to
# GitHub so pushes to main deploy automatically.
set -euo pipefail

: "${APP_NAME:?Set APP_NAME, a globally unique name, e.g. aig-expenses-nick}"
: "${ALLOWED_EMAILS:?Set ALLOWED_EMAILS, the Microsoft account emails allowed to sign in, comma-separated}"
RG="${RG:-aig-expenses-rg}"
LOCATION="${LOCATION:-canadacentral}"
SKU="${SKU:-B1}"   # B1 is about US$13/month and stays awake; F1 is free but sleeps and is slow to wake
URL="https://${APP_NAME}.azurewebsites.net"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  read -rsp "Anthropic API key for reading receipts (leave empty to add later): " ANTHROPIC_API_KEY; echo
fi

echo "== Resource group, plan and web app"
az group create -n "$RG" -l "$LOCATION" -o none
az appservice plan create -g "$RG" -n "${APP_NAME}-plan" --is-linux --sku "$SKU" -o none
az webapp create -g "$RG" -p "${APP_NAME}-plan" -n "$APP_NAME" --runtime "PYTHON:3.12" -o none
az webapp update -g "$RG" -n "$APP_NAME" --https-only true -o none

echo "== Microsoft sign-in (app registration)"
CLIENT_ID=$(az ad app create --display-name "AIG expenses (${APP_NAME})" \
  --sign-in-audience AzureADMyOrg --web-redirect-uris "${URL}/auth/callback" --query appId -o tsv)
# Personal Microsoft accounts (outlook.com, hotmail.com) need v2 tokens before the audience can include them.
az ad app update --id "$CLIENT_ID" --set api.requestedAccessTokenVersion=2
az ad app update --id "$CLIENT_ID" --sign-in-audience AzureADandPersonalMicrosoftAccount
# Microsoft Graph delegated permissions: openid, profile, email, offline_access, User.Read, Files.ReadWrite
az ad app permission add --id "$CLIENT_ID" --api 00000003-0000-0000-c000-000000000000 --api-permissions \
  37f7f235-527c-4136-accd-4a02d197296e=Scope 14dad69e-099b-42c9-810b-d002981feec1=Scope \
  64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0=Scope 7427e0e9-2fba-42fe-b0c0-848c9e6a8182=Scope \
  e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope 5c28f0bf-8a70-41f1-8ab2-9032436ddb65=Scope -o none \
  || echo "   (couldn't pre-list Graph permissions; you'll simply be asked to consent at first sign-in)"
CLIENT_SECRET=$(az ad app credential reset --id "$CLIENT_ID" --display-name "app-service" --years 2 --query password -o tsv)

echo "== App settings"
az webapp config appsettings set -g "$RG" -n "$APP_NAME" -o none --settings \
  MS_CLIENT_ID="$CLIENT_ID" MS_CLIENT_SECRET="$CLIENT_SECRET" MS_TENANT=common \
  ALLOWED_EMAILS="$ALLOWED_EMAILS" SESSION_SECRET="$(openssl rand -hex 32)" PUBLIC_URL="$URL" \
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" EXPENSES_DATA_DIR=/home/data \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true WEBSITES_ENABLE_APP_SERVICE_STORAGE=true
az webapp config set -g "$RG" -n "$APP_NAME" -o none \
  --startup-file "python -m uvicorn expenses.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"
if [[ "$SKU" != "F1" ]]; then az webapp config set -g "$RG" -n "$APP_NAME" --always-on true -o none; fi

echo "== Deployment credentials for GitHub Actions"
az resource update -g "$RG" --name scm --namespace Microsoft.Web --resource-type basicPublishingCredentialsPolicies \
  --parent "sites/${APP_NAME}" --set properties.allow=true -o none
PROFILE_FILE="$HOME/${APP_NAME}.publish-profile.xml"
az webapp deployment list-publishing-profiles -g "$RG" -n "$APP_NAME" --xml > "$PROFILE_FILE"

cat <<DONE

Done. Your site will be at: ${URL}

Last step, in GitHub (github.com/nicknastic/AIG-ERP > Settings > Secrets and variables > Actions):
  1. New repository secret   AZURE_WEBAPP_PUBLISH_PROFILE   = the contents of ${PROFILE_FILE}
     (show it with:  cat ${PROFILE_FILE})
  2. Variables tab > New variable   AZURE_WEBAPP_NAME   = ${APP_NAME}
Then run the "Deploy to Azure" workflow (Actions tab), or push to main.

After it deploys: open ${URL}, sign in, go to Settings and press "Connect OneDrive".
DONE
