$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectDir ".env"
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

# item 3.2 do relatório de análise: backup diário do PostgreSQL.
# Uso: agende esta chamada no Agendador de Tarefas do Windows
#   (ex.: diariamente às 02:00) apontando para este script.
$postgresPassword = $env:POSTGRES_PASSWORD
if ([string]::IsNullOrWhiteSpace($postgresPassword)) {
    $postgresPassword = Get-DotEnvValue -Path $envPath -Key "POSTGRES_PASSWORD"
}
if ([string]::IsNullOrWhiteSpace($postgresPassword)) {
    throw "Defina POSTGRES_PASSWORD no .env ou no ambiente antes de rodar o backup."
}

New-Item -ItemType Directory -Path $backupsDir -Force | Out-Null
$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$backupPath = Join-Path $backupsDir "investimentos_$timestamp.dump"

$dockerPath = Resolve-DockerCli
Write-Output "Gerando backup em $backupPath ..."
& $dockerPath compose --project-directory $projectDir exec -T `
    -e PGPASSWORD=$postgresPassword `
    db pg_dump -U investimentos -d investimentos --format=custom --file=/tmp/backup.dump
if ($LASTEXITCODE -ne 0) {
    throw "pg_dump falhou dentro do contêiner do banco de dados."
}
& $dockerPath compose --project-directory $projectDir cp "db:/tmp/backup.dump" $backupPath
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao copiar o backup para fora do contêiner."
}
& $dockerPath compose --project-directory $projectDir exec -T db rm -f /tmp/backup.dump

$cutoff = (Get-Date).AddDays(-$retentionDays)
Get-ChildItem -LiteralPath $backupsDir -Filter "investimentos_*.dump" |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    Remove-Item -Force

Write-Output "Backup concluído: $backupPath"
Write-Output "Backups com mais de $retentionDays dias foram removidos."
