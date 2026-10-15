$ErrorActionPreference = 'Stop'

function Assert-AuthKeyEncoding {
    param([string] $Value, [string] $Name)

    $message = "$Name must be canonical Base64 encoding of exactly 32 bytes (44 characters); hexadecimal keys are not supported."
    if ($Value.Length -ne 44) { throw $message }
    try {
        $bytes = [Convert]::FromBase64String($Value)
    }
    catch [FormatException] {
        throw $message
    }
    if ($bytes.Length -ne 32 -or [Convert]::ToBase64String($bytes) -cne $Value) {
        throw $message
    }
}

$signing = [Environment]::GetEnvironmentVariable('WEBUI_AUTH_SIGNING_KEY', 'Process')
$encryption = [Environment]::GetEnvironmentVariable('WEBUI_AUTH_ENCRYPTION_KEY', 'Process')
Assert-AuthKeyEncoding $signing 'WEBUI_AUTH_SIGNING_KEY'
Assert-AuthKeyEncoding $encryption 'WEBUI_AUTH_ENCRYPTION_KEY'
if ($signing -ceq $encryption) { throw 'Signing and encryption keys must be independent, distinct values.' }
