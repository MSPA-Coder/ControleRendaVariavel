<#
.SYNOPSIS
  Cria, uma única vez, o segredo compartilhado entre o agente Windows e o VPS.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$secretsDir = Join-Path $projectDir ".secrets"
$tokenPath = Join-Path $secretsDir "collector_agent_token"

if (Test-Path -LiteralPath $tokenPath -PathType Leaf) {
    Write-Output "O segredo do agente já existe. Nenhum valor foi exibido."
    exit 0
}

New-Item -ItemType Directory -Path $secretsDir -Force | Out-Null
$bytes = [byte[]]::new(32)
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$token = [System.Convert]::ToHexString($bytes).ToLowerInvariant()
[System.IO.File]::WriteAllText($tokenPath, $token, [System.Text.UTF8Encoding]::new($false))
Write-Output "Segredo do agente criado em .secrets. Nenhum valor foi exibido."
