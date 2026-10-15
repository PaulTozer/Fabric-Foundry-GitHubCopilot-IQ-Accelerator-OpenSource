param(
    [Parameter(Mandatory)]
    [ValidateSet('Prepare', 'Validate', 'ValidateDeployed')]
    [string] $Mode
)

$ErrorActionPreference = 'Stop'
$flag = [Environment]::GetEnvironmentVariable('FABRIC_DEPLOY_TEST_ONTOLOGY', 'Process')
if ([string]::IsNullOrEmpty($flag)) { $flag = '0' }
if ($flag -notin @('0', '1')) { throw 'FABRIC_DEPLOY_TEST_ONTOLOGY must be 0 or 1 (default 0).' }

function Set-SampleStatus {
    param([string] $Status)
    $null = & azd env set FABRIC_TEST_PROVISIONING_STATUS $Status --environment $env:AZURE_ENV_NAME
    if ($LASTEXITCODE -ne 0) { throw 'Cannot record sample preparation status in the selected azd environment.' }
}

if ($Mode -eq 'Prepare' -and $flag -eq '1') {
    & "$PSScriptRoot\Assert-AuthKeyInputs.ps1"
    if ($env:AZURE_ENV_NAME -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,63}$') {
        throw 'AZURE_ENV_NAME must identify the explicit deployment environment.'
    }
    Set-SampleStatus 'preparing'
}

$output = & python "$PSScriptRoot\fabric_test_provisioning.py" --mode $Mode
if ($LASTEXITCODE -ne 0) {
    if ($Mode -eq 'Prepare' -and $flag -eq '1') { Set-SampleStatus 'failed' }
    throw 'Fabric preparation failed. Resolve the stage-specific error before retrying; no fallback was selected.'
}
$result = ($output -join "`n") | ConvertFrom-Json
if ($Mode -eq 'Prepare' -and $flag -eq '1') {
    if ($result.status -ne 'ready') { throw 'Sample preparation did not return a verified ready state.' }
    $keys = @(
        'FABRIC_TEST_WORKSPACE_ID', 'FABRIC_TEST_LAKEHOUSE_ID', 'FABRIC_TEST_ONTOLOGY_ID',
        'FABRIC_TEST_SQL_ENDPOINT', 'FABRIC_TEST_SQL_DATABASE', 'FABRIC_TEST_ONTOLOGY_MCP_URL',
        'FABRIC_TEST_SCHEMA_VERSION', 'FABRIC_TEST_PROVISIONING_STATUS'
    )
    $properties = @($result.outputs.PSObject.Properties)
    if ($properties.Count -ne $keys.Count -or @($properties | Where-Object { $_.Name -notin $keys }).Count) {
        throw 'Sample preparation returned unexpected output keys; original integration inputs must remain untouched.'
    }
    $returnedWorkspace = [guid]::Empty
    $selectedWorkspace = [guid]::Empty
    if (-not [guid]::TryParse([string]$result.outputs.FABRIC_TEST_WORKSPACE_ID, [ref]$returnedWorkspace) -or
        -not [guid]::TryParse($env:FABRIC_TEST_WORKSPACE_ID, [ref]$selectedWorkspace) -or
        $returnedWorkspace -ne $selectedWorkspace -or
        $result.outputs.FABRIC_TEST_PROVISIONING_STATUS -ne 'ready') {
        throw 'Sample output does not match the explicit workspace and ready state.'
    }
    foreach ($key in $keys | Where-Object { $_ -ne 'FABRIC_TEST_PROVISIONING_STATUS' }) {
        $value = $result.outputs.$key
        if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
            throw "Sample output $key is missing."
        }
        $null = & azd env set $key $value --environment $env:AZURE_ENV_NAME
        if ($LASTEXITCODE -ne 0) { throw "Cannot persist generated nonsecret setting $key; ready state was not published." }
    }
    Set-SampleStatus 'ready'
}
Write-Output "Fabric preparation: $($result.status) ($Mode); configuration presence is not runtime permission or service-health proof."
