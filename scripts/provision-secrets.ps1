<#!
.SYNOPSIS
  Migra os segredos atuais de .env para arquivos locais consumidos pelo Compose.

.DESCRIPTION
  Cria .secrets\secret_key e .secrets\postgres_password sem alterar .env,
  banco, contêineres ou valores existentes. Não imprime conteúdos. Por padrão,
  recusa sobrescrever arquivos para que a migração seja revisável; use -Force
  somente ao rotacionar deliberadamente o arquivo a partir de .env.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$Force)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectDir ".env"
$secretsDir = Join-Path $projectDir ".secrets"

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Arquivo .env não encontrado. Copie .env.example e defina os segredos antes de provisionar."
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Length -eq 2 -and $parts[0].Trim() -eq $Key) {
            $value = $parts[1].Trim().Trim('"').Trim("'")
            if ([string]::IsNullOrWhiteSpace($value)) {
                throw "$Key não pode estar vazio."
            }
            return $value
        }
    }
    throw "$Key não foi definido no .env."
}

function Write-SecretFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if ((Test-Path -LiteralPath $Path) -and -not $Force) {
        throw "Arquivo de segredo já existe: $Path. Revise-o ou use -Force para substituir deliberadamente."
    }
    if ($PSCmdlet.ShouldProcess($Path, "gravar segredo local")) {
        $temporary = Join-Path $secretsDir ".$([IO.Path]::GetFileName($Path)).$([guid]::NewGuid().ToString('N')).tmp"
        try {
            [System.IO.File]::WriteAllText(
                $temporary,
                $Value,
                [System.Text.UTF8Encoding]::new($false)
            )
            Move-Item -LiteralPath $temporary -Destination $Path -Force
        }
        finally {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function New-ControlToken {
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [System.Convert]::ToHexString($bytes).ToLowerInvariant()
}

$secretKey = Get-DotEnvValue -Path $envPath -Key "SECRET_KEY"
$postgresPassword = Get-DotEnvValue -Path $envPath -Key "POSTGRES_PASSWORD"
$secretKeyPath = Join-Path $secretsDir "secret_key"
$postgresPasswordPath = Join-Path $secretsDir "postgres_password"

# Valida tudo antes da primeira escrita para evitar provisionamento parcial.
if (-not $Force) {
    foreach ($target in @($secretKeyPath, $postgresPasswordPath)) {
        if (Test-Path -LiteralPath $target) {
            throw "Arquivo de segredo já existe: $target. Revise-o ou use -Force para substituir deliberadamente."
        }
    }
}

if ($PSCmdlet.ShouldProcess($secretsDir, "criar diretório de segredos local")) {
    New-Item -ItemType Directory -Path $secretsDir -Force | Out-Null
}

Write-SecretFile -Path $secretKeyPath -Value $secretKey
Write-SecretFile -Path $postgresPasswordPath -Value $postgresPassword

$controlTokenPath = Join-Path $secretsDir "rtd_control_token"
if (-not (Test-Path -LiteralPath $controlTokenPath)) {
    Write-SecretFile -Path $controlTokenPath -Value (New-ControlToken)
}

$collectorAgentTokenPath = Join-Path $secretsDir "collector_agent_token"
if (-not (Test-Path -LiteralPath $collectorAgentTokenPath)) {
    Write-SecretFile -Path $collectorAgentTokenPath -Value (New-ControlToken)
}

Write-Output "Arquivos de segredo provisionados em .secrets. Nenhum valor foi exibido; revise permissões locais antes de iniciar a pilha."
