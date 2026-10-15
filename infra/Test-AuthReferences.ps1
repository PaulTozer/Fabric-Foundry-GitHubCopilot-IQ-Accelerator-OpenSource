$ErrorActionPreference = 'Stop'

function Assert-AuthReference {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

$signingFixture = [Convert]::ToBase64String([byte[]](0..31))
$encryptionFixture = [Convert]::ToBase64String([byte[]](32..63))
$cases = @(
    @{ Label = 'Canonical 32-byte Base64'; Signing = $signingFixture; Encryption = $encryptionFixture; Valid = $true }
    @{ Label = 'Missing signing'; Signing = ''; Encryption = $encryptionFixture; Valid = $false }
    @{ Label = 'Missing encryption'; Signing = $signingFixture; Encryption = ''; Valid = $false }
    @{ Label = 'Hex signing'; Signing = ('a' * 64); Encryption = $encryptionFixture; Valid = $false }
    @{ Label = 'Hex encryption'; Signing = $signingFixture; Encryption = ('b' * 64); Valid = $false }
    @{ Label = 'Invalid alphabet'; Signing = ('!' * 43 + '='); Encryption = $encryptionFixture; Valid = $false }
    @{ Label = '31 decoded bytes'; Signing = [Convert]::ToBase64String([byte[]](0..30)); Encryption = $encryptionFixture; Valid = $false }
    @{ Label = '33 decoded bytes'; Signing = $signingFixture; Encryption = [Convert]::ToBase64String([byte[]](0..32)); Valid = $false }
    @{ Label = 'Noncanonical padding bits'; Signing = ('A' * 42 + 'B='); Encryption = $encryptionFixture; Valid = $false }
    @{ Label = 'Whitespace'; Signing = $signingFixture + "`n"; Encryption = $encryptionFixture; Valid = $false }
    @{ Label = 'Shared key material'; Signing = $signingFixture; Encryption = $signingFixture; Valid = $false }
)
$environmentNames = @('WEBUI_AUTH_SIGNING_KEY', 'WEBUI_AUTH_ENCRYPTION_KEY')
$previous = @{}
foreach ($name in $environmentNames) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    foreach ($case in $cases) {
        [Environment]::SetEnvironmentVariable($environmentNames[0], $case.Signing, 'Process')
        [Environment]::SetEnvironmentVariable($environmentNames[1], $case.Encryption, 'Process')
        $accepted = $true
        try { & "$PSScriptRoot\Assert-AuthKeyInputs.ps1" }
        catch { $accepted = $false }
        Assert-AuthReference ($accepted -eq $case.Valid) "Key format regression failed: $($case.Label)."
    }
}
finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}

$compiled = & az bicep build --file "$PSScriptRoot\main.bicep" --stdout
if ($LASTEXITCODE -ne 0) { throw 'Bicep compilation failed.' }
$template = ($compiled -join "`n") | ConvertFrom-Json -Depth 100
$module = @($template.resources | Where-Object { $_.type -eq 'Microsoft.Resources/deployments' })[0]
$nested = $module.properties.template
$ui = @($nested.resources | Where-Object { $_.type -eq 'Microsoft.App/containerApps' -and $_.name -eq "[variables('webuiAppName')]" })[0]
$auth = @($nested.resources | Where-Object { $_.type -eq 'Microsoft.App/containerApps/authConfigs' })[0]
$inputs = Get-Content -Raw "$PSScriptRoot\main.parameters.json" | ConvertFrom-Json
$azdConfig = Get-Content -Raw "$PSScriptRoot\..\azure.yaml"
Assert-AuthReference ($azdConfig -match '(?m)^  preprovision:\r?\n    - shell: pwsh\r?\n      run: \.\\infra\\Assert-AuthKeyInputs\.ps1\r?\n      interactive: false\r?\n      continueOnError: false\r?\n    - shell: pwsh\r?\n      run: \.\\infra\\Invoke-FabricPreparation\.ps1 -Mode Validate') 'azd provision must run the unchanged fail-closed semantic key-input preflight first.'

$pairs = @(
    @{ Name = 'webuiAuthSigningSecretName'; Key = 'webuiAuthSigningKey'; Env = 'WEBUI_AUTH_SIGNING_SECRET_NAME'; KeyEnv = 'WEBUI_AUTH_SIGNING_KEY'; Ref = 'containerAppAuthSigningSecretName' }
    @{ Name = 'webuiAuthEncryptionSecretName'; Key = 'webuiAuthEncryptionKey'; Env = 'WEBUI_AUTH_ENCRYPTION_SECRET_NAME'; KeyEnv = 'WEBUI_AUTH_ENCRYPTION_KEY'; Ref = 'containerAppAuthEncryptionSecretName' }
)
foreach ($pair in $pairs) {
    $name = $pair.Name
    $key = $pair.Key
    $constraint = $template.parameters.$name
    Assert-AuthReference ($null -ne $constraint -and $constraint.minLength -eq 1 -and $constraint.maxLength -eq 20 -and $null -eq $constraint.PSObject.Properties['defaultValue']) "$name must be required and bounded; generated authority must not be a fallback."
    Assert-AuthReference ($inputs.parameters.$name.value -eq ('${' + $pair.Env + '}')) "$name must require an explicit azd environment setting."
    Assert-AuthReference ($inputs.parameters.$key.value -eq ('${' + $pair.KeyEnv + '}')) "$key must require a protected current-value input."
    foreach ($scope in @($template, $nested)) {
        $keyConstraint = $scope.parameters.$key
        Assert-AuthReference ($keyConstraint.type -eq 'secureString' -and $keyConstraint.minLength -eq 44 -and $keyConstraint.maxLength -eq 44 -and $null -eq $keyConstraint.PSObject.Properties['defaultValue']) "$key must be a required 44-character secure input at both scopes."
    }
    Assert-AuthReference ($module.properties.parameters.$name.value -eq "[parameters('$name')]" -and $module.properties.parameters.$key.value -eq "[parameters('$key')]") "$name/$key must be passed through without replacement."
    Assert-AuthReference ($auth.properties.encryptionSettings.($pair.Ref) -eq "[parameters('$name')]") "$name must always bind the authConfig."
    $definition = @($ui.properties.configuration.secrets | Where-Object { $_.name -eq "[parameters('$name')]" })[0]
    Assert-AuthReference ($null -ne $definition -and $definition.value -eq "[parameters('$key')]") "$name must resupply the current secure value, never use a name-only or regenerated fallback."
}

$secretNames = @($ui.properties.configuration.secrets | ForEach-Object { $_.name })
Assert-AuthReference ($secretNames -contains 'mcp-service-token' -and $secretNames -contains 'entra-client-secret') 'Existing MCP and Entra secret references must be retained.'
Assert-AuthReference ($auth.properties.platform.enabled -eq $true -and $auth.properties.globalValidation.unauthenticatedClientAction -eq 'RedirectToLoginPage' -and $auth.properties.login.tokenStore.enabled -eq $false) 'Authentication protection and token-store policy must not change.'
$browserEnv = $ui.properties.template.containers[0].env | ConvertTo-Json -Depth 10
Assert-AuthReference ($browserEnv -notmatch 'webuiAuth(Signing|Encryption)') 'Custom authority keys must not become browser/runtime environment variables.'
$outputs = @($template.outputs, $nested.outputs) | ConvertTo-Json -Depth 10
Assert-AuthReference ($outputs -notmatch 'webuiAuth(Signing|Encryption)Key') 'Custom authority values must not become deployment outputs.'

$flag = $template.parameters.fabricDeployTestOntology
Assert-AuthReference ($flag.defaultValue -eq '0' -and ($flag.allowedValues -join ',') -eq '0,1') 'Compiled sample flag must default off and accept only 0/1.'
Assert-AuthReference ($template.variables.sampleSelected -eq "[equals(parameters('fabricDeployTestOntology'), '1')]") 'Only flag 1 may select sample references.'
$selection = [regex]::Match($template.variables.selectedFabric, "^\[if\(variables\('sampleSelected'\), createObject\((?<sample>.*)\), createObject\((?<existing>.*)\)\)\]$")
Assert-AuthReference $selection.Success 'Compiled Fabric selection must use separate explicit sample and original objects.'
$references = @(
    @('workspaceId', 'fabricWorkspaceId', 'fabricTestWorkspaceId', 'WORKSPACE_ID'),
    @('lakehouseId', 'fabricLakehouseId', 'fabricTestLakehouseId', 'LAKEHOUSE_ID'),
    @('ontologyId', 'fabricOntologyId', 'fabricTestOntologyId', 'ONTOLOGY_ID'),
    @('sqlEndpoint', 'fabricSqlEndpoint', 'fabricTestSqlEndpoint', 'SQL_ENDPOINT'),
    @('sqlDatabase', 'fabricSqlDatabase', 'fabricTestSqlDatabase', 'SQL_DATABASE'),
    @('ontologyMcpUrl', 'fabricOntologyMcpUrl', 'fabricTestOntologyMcpUrl', 'ONTOLOGY_MCP_URL')
)
foreach ($reference in $references) {
    $property, $original, $sample, $suffix = $reference
    Assert-AuthReference ($selection.Groups['sample'].Value.Contains("'$property', parameters('$sample')")) 'Sample branch must use only generated references.'
    Assert-AuthReference ($selection.Groups['existing'].Value.Contains("'$property', parameters('$original')")) 'Default branch must preserve every original reference.'
    Assert-AuthReference ($module.properties.parameters.$original.value -eq "[variables('selectedFabric').$property]") 'Selected references must reach the runtime module.'
    Assert-AuthReference ($template.outputs.("FABRIC_ACTIVE_$suffix").value -eq "[variables('selectedFabric').$property]") 'Active outputs must report every selected runtime reference.'
}
foreach ($branch in @('sample', 'existing')) {
    Assert-AuthReference (([regex]::Matches($selection.Groups[$branch].Value, 'parameters\(')).Count -eq 6) 'Fabric branches must select exactly the original six integration fields.'
}
Assert-AuthReference ($module.properties.parameters.fabricDataMode.value -eq "[variables('fabricDataMode')]") 'Synthetic provenance must reach the backend.'
Assert-AuthReference ($template.parameters.iqDemoMode.defaultValue -eq '0' -and $module.properties.parameters.iqDemoMode.value -eq "[parameters('iqDemoMode')]") 'Demo mode must stay separate and off by default.'

Write-Output 'Auth-reference compilation, Base64 preservation and compiled default/sample runtime selection checks passed.'
