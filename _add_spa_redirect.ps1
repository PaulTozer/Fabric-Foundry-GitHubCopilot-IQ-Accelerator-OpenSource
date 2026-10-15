# One-off: register the webui page origin as an SPA redirect URI on the
# EnterpriseIQ Entra app registration. Required so MSAL.js (auth code + PKCE)
# can complete acquireTokenPopup without hitting AADSTS50011.
#
# Usage:  pwsh -File ./_add_spa_redirect.ps1
#
# Safe to re-run: only appends the origin if missing.

$ErrorActionPreference = 'Stop'

# Pull the FQDN from the azd env (bicep outputs); pull the Entra client
# ID from the deployed Easy Auth config (it isn't stored in azd's .env).
$envVals = @{}
azd env get-values | ForEach-Object {
    if ($_ -match '^([^=]+)="?([^"]*)"?$') {
        $envVals[$Matches[1]] = $Matches[2]
    }
}

$rg       = $envVals['AZURE_RESOURCE_GROUP']
$fqdn     = $envVals['WEBUI_FQDN']
if (-not $rg)   { throw "AZURE_RESOURCE_GROUP not in azd env. Run 'azd provision' first." }
if (-not $fqdn) { throw "WEBUI_FQDN not in azd env. Run 'azd provision' first." }

# The webui Container App's name is deterministic from the azd env name.
$webuiApp = "ca-$($envVals['AZURE_ENV_NAME'])-webui"
$clientId = az containerapp auth show --name $webuiApp --resource-group $rg --query "identityProviders.azureActiveDirectory.registration.clientId" -o tsv
if (-not $clientId) { throw "Could not read Entra client ID from Easy Auth config on $webuiApp. Has the app been deployed yet?" }
if (-not $clientId) { throw "ENTRA_CLIENT_ID not set in azd env. Run 'azd env set ENTRA_CLIENT_ID <guid>' first." }
if (-not $fqdn)     { throw "WEBUI_FQDN not in azd env outputs. Run 'azd provision' first." }

$origin = "https://$fqdn/"
Write-Host "Client ID: $clientId"
Write-Host "Origin:    $origin"

$objectId = az ad app show --id $clientId --query id -o tsv
if (-not $objectId) { throw "Could not resolve app object id for $clientId. Are you signed in to the right tenant? (az login --tenant ...)" }
Write-Host "Object ID: $objectId"

# Read existing SPA redirect URIs and append the origin if missing.
$currentJson = az rest --method GET --uri "https://graph.microsoft.com/v1.0/applications/$objectId" --query "spa.redirectUris"
$current = if ($currentJson) { $currentJson | ConvertFrom-Json } else { @() }
if ($current -isnot [System.Array]) { $current = @($current) }
if ($current -contains $origin) {
    Write-Host "SPA redirect URI already registered. Nothing to do."
    return
}
$updated = @($current) + $origin
Write-Host "Updating SPA redirect URIs to:"
$updated | ForEach-Object { Write-Host "  - $_" }

$bodyJson = @{ spa = @{ redirectUris = $updated } } | ConvertTo-Json -Depth 5 -Compress
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value $bodyJson -Encoding utf8
try {
    az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$objectId" --headers "Content-Type=application/json" --body "@$($tmp.FullName)"
} finally {
    Remove-Item $tmp -Force
}

Write-Host "`nDone. Verify:"
az rest --method GET --uri "https://graph.microsoft.com/v1.0/applications/$objectId" --query "spa.redirectUris"
