<#!
.SYNOPSIS
  Provisiona os arquivos locais de segredo consumidos pelo Compose.

.DESCRIPTION
  Cria os segredos locais consumidos pelo Compose sem alterar .env por padrão,
  banco, contêineres ou valores existentes. Não imprime conteúdos. Por padrão,
  preserva arquivos existentes; para uma instalação antiga, ainda aceita
  SECRET_KEY e POSTGRES_PASSWORD do .env como migração única. Instalações novas
  geram os dois valores diretamente em .secrets. Use -Force somente ao
  rotacionar deliberadamente os arquivos. Use -MigrateDotEnv para substituir
  valores legados do .env por caminhos para os arquivos provisionados.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Force,
    [switch]$MigrateDotEnv
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectDir ".env"
$secretsDir = Join-Path $projectDir ".secrets"

function Get-OptionalDotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
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
                return $null
            }
            return $value
        }
    }
    return $null
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

function Ensure-SecretFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if (-not (Test-Path -LiteralPath $Path) -or $Force) {
        Write-SecretFile -Path $Path -Value $Value
    }
}

function New-ControlToken {
    $bytes = [byte[]]::new(32)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return ([System.BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

function Migrate-DotEnvSecretPaths {
    if (-not $MigrateDotEnv -or -not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        return
    }

    $pathByKey = @{
        "SECRET_KEY" = "SECRET_KEY_FILE=.secrets/secret_key"
        "POSTGRES_PASSWORD" = "POSTGRES_PASSWORD_FILE=.secrets/postgres_password"
        "DATABASE_URL" = "DATABASE_URL_FILE=.secrets/database_url"
    }
    $seenPaths = @{}
    $output = [System.Collections.Generic.List[string]]::new()
    foreach ($line in Get-Content -LiteralPath $envPath) {
        $trimmed = $line.Trim()
        if ($trimmed -match "^([A-Z0-9_]+)=") {
            $key = $Matches[1]
            if ($pathByKey.ContainsKey($key)) {
                $pathKey = "${key}_FILE"
                if ($key -eq "DATABASE_URL" -and -not (Test-Path -LiteralPath (Join-Path $secretsDir "database_url") -PathType Leaf)) {
                    $value = $trimmed.Split("=", 2)[1].Trim().Trim('"').Trim("'")
                    if ([string]::IsNullOrWhiteSpace($value)) {
                        throw "DATABASE_URL não pode estar vazio durante a migração."
                    }
                    Ensure-SecretFile -Path (Join-Path $secretsDir "database_url") -Value $value
                }
                if ($seenPaths.ContainsKey($pathKey)) {
                    continue
                }
                $output.Add($pathByKey[$key])
                $seenPaths[$pathKey] = $true
                continue
            }
            if ($key -like "*_FILE") {
                if ($seenPaths.ContainsKey($key)) {
                    continue
                }
                $seenPaths[$key] = $true
            }
        }
        $output.Add($line)
    }
    foreach ($key in $pathByKey.Keys) {
        $pathKey = "${key}_FILE"
        if (-not $seenPaths.ContainsKey($pathKey)) {
            if ($key -eq "DATABASE_URL" -and -not (Test-Path -LiteralPath (Join-Path $secretsDir "database_url") -PathType Leaf)) {
                continue
            }
            $output.Add($pathByKey[$key])
        }
    }
    if ($PSCmdlet.ShouldProcess($envPath, "remover valores de segredo do .env")) {
        $temporary = Join-Path $projectDir ".env.$([guid]::NewGuid().ToString('N')).tmp"
        try {
            [System.IO.File]::WriteAllLines($temporary, [string[]]$output, [System.Text.UTF8Encoding]::new($false))
            Move-Item -LiteralPath $temporary -Destination $envPath -Force
        }
        finally {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($Force) {
    # Rotação deliberada não pode reaproveitar a semente legada do `.env`.
    # O operador precisa tratar a senha no PostgreSQL e invalidar as sessões
    # depois de usar este modo.
    $secretKey = New-ControlToken
    $postgresPassword = New-ControlToken
}
else {
    $secretKey = Get-OptionalDotEnvValue -Path $envPath -Key "SECRET_KEY"
    $postgresPassword = Get-OptionalDotEnvValue -Path $envPath -Key "POSTGRES_PASSWORD"
    if ([string]::IsNullOrWhiteSpace($secretKey)) { $secretKey = New-ControlToken }
    if ([string]::IsNullOrWhiteSpace($postgresPassword)) { $postgresPassword = New-ControlToken }
}
$secretKeyPath = Join-Path $secretsDir "secret_key"
$postgresPasswordPath = Join-Path $secretsDir "postgres_password"
$postgresAppPasswordPath = Join-Path $secretsDir "postgres_app_password"
$qualityPasswordPath = Join-Path $secretsDir "postgres_password_quality"
$qualityAppPasswordPath = Join-Path $secretsDir "postgres_app_password_quality"

if ($PSCmdlet.ShouldProcess($secretsDir, "criar diretório de segredos local")) {
    New-Item -ItemType Directory -Path $secretsDir -Force | Out-Null
}

Ensure-SecretFile -Path $secretKeyPath -Value $secretKey
Ensure-SecretFile -Path $postgresPasswordPath -Value $postgresPassword
Ensure-SecretFile -Path $postgresAppPasswordPath -Value (New-ControlToken)
Ensure-SecretFile -Path $qualityPasswordPath -Value (New-ControlToken)
Ensure-SecretFile -Path $qualityAppPasswordPath -Value (New-ControlToken)

$collectorAgentReadTokenPath = Join-Path $secretsDir "collector_agent_read_token"
Ensure-SecretFile -Path $collectorAgentReadTokenPath -Value (New-ControlToken)
$collectorAgentWriteTokenPath = Join-Path $secretsDir "collector_agent_write_token"
Ensure-SecretFile -Path $collectorAgentWriteTokenPath -Value (New-ControlToken)
Migrate-DotEnvSecretPaths

Write-Output "Arquivos de segredo provisionados em .secrets. Nenhum valor foi exibido; revise permissões locais antes de iniciar a pilha."
