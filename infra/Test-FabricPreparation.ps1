$ErrorActionPreference = 'Stop'

function Assert-Sample {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

$names = @(
    'FABRIC_DEPLOY_TEST_ONTOLOGY', 'FABRIC_TEST_WORKSPACE_ID', 'AZURE_ENV_NAME',
    'WEBUI_AUTH_SIGNING_KEY', 'WEBUI_AUTH_ENCRYPTION_KEY', 'FABRIC_WORKSPACE_ID',
    'FABRIC_LAKEHOUSE_ID', 'FABRIC_ONTOLOGY_ID', 'FABRIC_SQL_ENDPOINT',
    'FABRIC_SQL_DATABASE', 'FABRIC_ONTOLOGY_MCP_URL', 'FABRIC_TEST_LAKEHOUSE_ID',
    'FABRIC_TEST_ONTOLOGY_ID', 'FABRIC_TEST_SQL_ENDPOINT', 'FABRIC_TEST_SQL_DATABASE',
    'FABRIC_TEST_ONTOLOGY_MCP_URL', 'FABRIC_TEST_SCHEMA_VERSION', 'FABRIC_TEST_PROVISIONING_STATUS'
)
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$script:calls = [System.Collections.Generic.List[object]]::new()
$global:FabricPreparationTestState = @{ Calls = $script:calls; Failure = $false; BadOutput = $false }

function python {
    $global:FabricPreparationTestState.Calls.Add(@('python', $args[-1]))
    $global:LASTEXITCODE = 0
    if ($global:FabricPreparationTestState.Failure) { $global:LASTEXITCODE = 1; return }
    $outputs = @{}
    $status = 'unconfigured'
    if ($env:FABRIC_DEPLOY_TEST_ONTOLOGY -eq '1') {
        $status = 'ready'
        if ($args[-1] -eq 'Prepare') {
            $outputs = @{
                FABRIC_TEST_WORKSPACE_ID = $env:FABRIC_TEST_WORKSPACE_ID.ToLowerInvariant()
                FABRIC_TEST_LAKEHOUSE_ID = '22222222-2222-4222-8222-222222222222'
                FABRIC_TEST_ONTOLOGY_ID = '44444444-4444-4444-8444-444444444444'
                FABRIC_TEST_SQL_ENDPOINT = 'sample.sql.example'
                FABRIC_TEST_SQL_DATABASE = 'SampleLakehouse'
                FABRIC_TEST_ONTOLOGY_MCP_URL = 'https://api.fabric.microsoft.com/v1/mcp/test'
                FABRIC_TEST_SCHEMA_VERSION = 'v1'
                FABRIC_TEST_PROVISIONING_STATUS = 'ready'
            }
            if ($global:FabricPreparationTestState.BadOutput) { $outputs['FABRIC_LAKEHOUSE_ID'] = 'must-not-replace' }
        }
    }
    @{status = $status; outputs = $outputs} | ConvertTo-Json
}

function azd {
    $global:FabricPreparationTestState.Calls.Add(@('azd') + $args)
    $global:LASTEXITCODE = 0
    Assert-Sample ($args[0] -eq 'env' -and $args[1] -eq 'set') 'Only nonsecret generated env updates are allowed.'
    Assert-Sample ($args[2] -like 'FABRIC_TEST_*') 'Original FABRIC inputs must not be written.'
    Assert-Sample ($args[4] -eq '--environment' -and $args[5] -eq 'offline-feature') 'Settings must use the explicit environment.'
}

try {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, '', 'Process') }
    $env:AZURE_ENV_NAME = 'offline-feature'
    $env:FABRIC_TEST_WORKSPACE_ID = '11111111-1111-4111-8111-111111111111'
    $env:FABRIC_LAKEHOUSE_ID = 'original-operator-value'
    $env:WEBUI_AUTH_SIGNING_KEY = [Convert]::ToBase64String([byte[]](0..31))
    $env:WEBUI_AUTH_ENCRYPTION_KEY = [Convert]::ToBase64String([byte[]](32..63))
    foreach ($mode in @('Prepare', 'Validate', 'ValidateDeployed')) {
        $script:calls.Clear()
        & "$PSScriptRoot\Invoke-FabricPreparation.ps1" -Mode $mode | Out-Null
        Assert-Sample ($script:calls.Count -eq 1 -and $script:calls[0][0] -eq 'python') 'Default mode must not write azd/sample state or run cloud CLI.'
    }
    $env:FABRIC_DEPLOY_TEST_ONTOLOGY = '1'
    $script:calls.Clear()
    $env:WEBUI_AUTH_SIGNING_KEY = 'invalid-inactive-fixture'
    $blocked = $false
    try { & "$PSScriptRoot\Invoke-FabricPreparation.ps1" -Mode Prepare | Out-Null }
    catch { $blocked = $true }
    Assert-Sample ($blocked -and $script:calls.Count -eq 0) 'Auth preflight must block before preparation/status writes.'
    $env:WEBUI_AUTH_SIGNING_KEY = [Convert]::ToBase64String([byte[]](0..31))
    & "$PSScriptRoot\Invoke-FabricPreparation.ps1" -Mode Prepare | Out-Null
    Assert-Sample ($script:calls[0][2] -eq 'set' -and $script:calls[0][4] -eq 'preparing') 'Preparation must invalidate stale ready status first.'
    Assert-Sample ($script:calls[-1][3] -eq 'FABRIC_TEST_PROVISIONING_STATUS' -and $script:calls[-1][4] -eq 'ready') 'Ready must be published last.'
    Assert-Sample ($env:FABRIC_LAKEHOUSE_ID -eq 'original-operator-value') 'Original integration input changed.'
    $env:FABRIC_TEST_WORKSPACE_ID = 'AAAAAAAA-1111-4111-8111-111111111111'
    $script:calls.Clear()
    & "$PSScriptRoot\Invoke-FabricPreparation.ps1" -Mode Prepare | Out-Null
    Assert-Sample ($script:calls[-1][4] -eq 'ready') 'Equivalent canonical UUID case must not fail after preparation.'
    foreach ($mode in @('Validate', 'ValidateDeployed')) {
        $script:calls.Clear()
        & "$PSScriptRoot\Invoke-FabricPreparation.ps1" -Mode $mode | Out-Null
        Assert-Sample ($script:calls.Count -eq 1 -and $script:calls[0][0] -eq 'python') 'Preview/provision/deploy validation must be read-only.'
    }
    $global:FabricPreparationTestState.Failure = $true
    $script:calls.Clear()
    $blocked = $false
    try { & "$PSScriptRoot\Invoke-FabricPreparation.ps1" -Mode Prepare | Out-Null }
    catch { $blocked = $true }
    Assert-Sample ($blocked -and $script:calls[-1][4] -eq 'failed') 'Adapter failure must not publish ready.'
    $global:FabricPreparationTestState.Failure = $false
    $global:FabricPreparationTestState.BadOutput = $true
    $blocked = $false
    try { & "$PSScriptRoot\Invoke-FabricPreparation.ps1" -Mode Prepare | Out-Null }
    catch { $blocked = $true }
    Assert-Sample ($blocked -and $env:FABRIC_LAKEHOUSE_ID -eq 'original-operator-value') 'Unexpected output must fail rather than replace existing references.'
}
finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    Remove-Item Function:python, Function:azd
    Remove-Variable FabricPreparationTestState -Scope Global
}

$yaml = Get-Content -LiteralPath "$PSScriptRoot\..\azure.yaml" -Raw
Assert-Sample ($yaml -match '(?m)^  preup:\r?\n    shell: pwsh\r?\n    run: \.\\infra\\Invoke-FabricPreparation.ps1 -Mode Prepare\r?\n    interactive: false\r?\n    continueOnError: false') 'Only preup may invoke preparation.'
Assert-Sample ($yaml -match '(?m)^  predeploy:\r?\n    shell: pwsh\r?\n    run: \.\\infra\\Invoke-FabricPreparation.ps1 -Mode ValidateDeployed') 'Image-only deploy must validate without writes.'
$inputs = Get-Content -LiteralPath "$PSScriptRoot\main.parameters.json" -Raw | ConvertFrom-Json
Assert-Sample ($inputs.parameters.fabricDeployTestOntology.value -eq '${FABRIC_DEPLOY_TEST_ONTOLOGY=0}') 'Sample flag must default off.'
foreach ($pair in @(
    @('fabricWorkspaceId', 'FABRIC_WORKSPACE_ID'), @('fabricLakehouseId', 'FABRIC_LAKEHOUSE_ID'),
    @('fabricOntologyId', 'FABRIC_ONTOLOGY_ID'), @('fabricSqlEndpoint', 'FABRIC_SQL_ENDPOINT'),
    @('fabricOntologyMcpUrl', 'FABRIC_ONTOLOGY_MCP_URL')
)) {
    Assert-Sample ($inputs.parameters.($pair[0]).value -eq ('${' + $pair[1] + '=}')) 'Original parameter mapping changed.'
}
Write-Output 'Offline Fabric hook ordering, zero-write defaults, auth-first, generated-output and read-only deployment checks passed.'
