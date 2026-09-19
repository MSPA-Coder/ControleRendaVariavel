<#
.SYNOPSIS
  Cria, uma única vez, os segredos separados de leitura e escrita do agente.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$secretsDir = Join-Path $projectDir ".secrets"
$readTokenPath = Join-Path $secretsDir "collector_agent_read_token"
$writeTokenPath = Join-Path $secretsDir "collector_agent_write_token"

New-Item -ItemType Directory -Path $secretsDir -Force | Out-Null
foreach ($tokenPath in @($readTokenPath, $writeTokenPath)) {
    if (Test-Path -LiteralPath $tokenPath -PathType Leaf) {
        continue
    }
    $bytes = [byte[]]::new(32)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    $token = (([System.BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant())
    [System.IO.File]::WriteAllText($tokenPath, $token, [System.Text.UTF8Encoding]::new($false))
}
Write-Output "Segredos separados do agente disponíveis em .secrets. Nenhum valor foi exibido."
