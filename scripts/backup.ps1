$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectDir ".env"
$secretsDir = Join-Path $projectDir ".secrets"
$backupsDir = Join-Path $projectDir "backups"
$retentionDays = 30

function Resolve-DockerCli {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"),
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw "Docker CLI não encontrado. Instale ou inicie o Docker Desktop."
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Length -eq 2 -and $parts[0].Trim() -eq $Key) {
            return $parts[1].Trim().Trim('"')
        }
    }
    return $null
}

function Get-SecretFileValue {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $value = [System.IO.File]::ReadAllText($Path).TrimEnd("`r", "`n")
    if ([string]::IsNullOrEmpty($value)) {
        throw "Arquivo de segredo vazio: $Path"
    }
    return $value
}

# Backup diário do PostgreSQL.
# Uso: agende esta chamada no Agendador de Tarefas do Windows
#   (ex.: diariamente às 02:00) apontando para este script.
$postgresPassword = $null
if (-not [string]::IsNullOrWhiteSpace($env:POSTGRES_PASSWORD_FILE)) {
    $postgresPassword = Get-SecretFileValue -Path $env:POSTGRES_PASSWORD_FILE
}
if ([string]::IsNullOrWhiteSpace($postgresPassword)) {
    $postgresPassword = Get-SecretFileValue -Path (Join-Path $secretsDir "postgres_password")
}
if ([string]::IsNullOrWhiteSpace($postgresPassword)) {
    $postgresPassword = $env:POSTGRES_PASSWORD
}
if ([string]::IsNullOrWhiteSpace($postgresPassword)) {
    $postgresPassword = Get-DotEnvValue -Path $envPath -Key "POSTGRES_PASSWORD"
}
if ([string]::IsNullOrWhiteSpace($postgresPassword)) {
    throw "Defina o arquivo postgres_password em .secrets, POSTGRES_PASSWORD_FILE ou POSTGRES_PASSWORD antes de rodar o backup."
}

New-Item -ItemType Directory -Path $backupsDir -Force | Out-Null
$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$backupPath = Join-Path $backupsDir "investimentos_$timestamp.dump"

$dockerPath = Resolve-DockerCli
Write-Output "Gerando backup em $backupPath ..."
# O dump atravessa stdout como bytes. `docker cp` não enxerga de forma
# confiável arquivos criados em tmpfs pelos contêineres Linux do Docker Desktop
# no Windows; copiar o fluxo binário evita esse ponto de falha sem expor senha
# ou montar o volume do PostgreSQL no host.
$process = [System.Diagnostics.Process]::new()
$process.StartInfo.FileName = $dockerPath
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.CreateNoWindow = $true
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.RedirectStandardError = $true
foreach ($argument in @(
    "compose", "--project-directory", $projectDir, "exec", "-T", "-e",
    "PGPASSWORD=$postgresPassword", "db", "pg_dump", "-U", "investimentos",
    "-d", "investimentos", "--format=custom"
)) {
    [void]$process.StartInfo.ArgumentList.Add($argument)
}
if (-not $process.Start()) {
    throw "Não foi possível iniciar pg_dump no contêiner do banco."
}
$destination = [System.IO.File]::Open(
    $backupPath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None
)
try {
    $process.StandardOutput.BaseStream.CopyTo($destination)
}
finally {
    $destination.Dispose()
}
$stderr = $process.StandardError.ReadToEnd()
$process.WaitForExit()
if ($process.ExitCode -ne 0) {
    Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
    throw "pg_dump falhou dentro do contêiner do banco: $stderr"
}

$cutoff = (Get-Date).AddDays(-$retentionDays)
Get-ChildItem -LiteralPath $backupsDir -Filter "investimentos_*.dump" |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    Remove-Item -Force

Write-Output "Backup concluído: $backupPath"
Write-Output "Backups com mais de $retentionDays dias foram removidos."
