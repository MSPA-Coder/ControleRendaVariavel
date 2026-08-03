Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectDir ".docker-local"
$TokenPath = Join-Path $RuntimeDir "rtd-control-token"
$ComposeEnvPath = Join-Path $RuntimeDir "rtd-control.env"
$PythonPath = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$RtdControlUri = "http://127.0.0.1:8765/state"

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
    throw "Docker CLI nao encontrado. Instale ou inicie o Docker Desktop."
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            $value = $matches[1]
            if ($value.Length -ge 2 -and (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            )) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

function Get-RtdControlToken {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    if (Test-Path -LiteralPath $TokenPath) {
        $token = (Get-Content -Raw -LiteralPath $TokenPath).Trim()
    } else {
        $tokenBytes = New-Object byte[] 32
        $random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $random.GetBytes($tokenBytes)
        } finally {
            $random.Dispose()
        }
        $token = [BitConverter]::ToString($tokenBytes).Replace("-", "")
        Set-Content -LiteralPath $TokenPath -Value $token -NoNewline
    }
    if ($token.Length -lt 32) {
        throw "Token do controlador RTD invalido em $TokenPath."
    }
    return $token
}

function Set-RtdControlComposeToken {
    param([Parameter(Mandatory = $true)][string]$Token)

    Set-Content -LiteralPath $ComposeEnvPath -Value "RTD_CONTROL_TOKEN=$Token" -NoNewline
    $env:RTD_CONTROL_TOKEN = $Token
}

function Set-RtdCollectorEnvironment {
    $envFile = Join-Path $ProjectDir ".env"
    if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
        $envDatabaseUrl = Get-DotEnvValue -Path $envFile -Name "DATABASE_URL"
        if (-not [string]::IsNullOrWhiteSpace($envDatabaseUrl)) {
            # Docker publishes the development PostgreSQL only on IPv4. After a
            # Windows restart, resolving localhost may try ::1 first and leave
            # the host collector blocked while PostgreSQL is otherwise healthy.
            $env:DATABASE_URL = $envDatabaseUrl -replace "@localhost(?=[:/])", "@127.0.0.1"
        } else {
            $postgresPassword = Get-DotEnvValue -Path $envFile -Name "POSTGRES_PASSWORD"
            if ([string]::IsNullOrWhiteSpace($postgresPassword)) {
                throw "Defina POSTGRES_PASSWORD ou DATABASE_URL em $envFile para o coletor RTD."
            }
            $encodedPassword = [uri]::EscapeDataString($postgresPassword)
            $env:DATABASE_URL = "postgresql+psycopg://investimentos:$encodedPassword@127.0.0.1:5435/investimentos"
        }
    }
    if ([string]::IsNullOrWhiteSpace($env:SECRET_KEY)) {
        $env:SECRET_KEY = Get-DotEnvValue -Path $envFile -Name "SECRET_KEY"
    }
    if ([string]::IsNullOrWhiteSpace($env:SECRET_KEY)) {
        throw "Defina SECRET_KEY em $envFile antes de iniciar o coletor RTD."
    }
}

function Test-RtdController {
    param([Parameter(Mandatory = $true)][string]$Token)

    try {
        $headers = @{ Authorization = "Bearer $Token" }
        $response = Invoke-RestMethod -Uri $RtdControlUri -Headers $headers -TimeoutSec 1
        return $null -ne $response.running
    } catch {
        return $false
    }
}

function Stop-RtdCollector {
    param([Parameter(Mandatory = $true)][string]$Token)

    try {
        $headers = @{ Authorization = "Bearer $Token" }
        Invoke-RestMethod -Uri $RtdControlUri -Method Post -Headers $headers `
            -ContentType "application/json" -Body '{"enabled":false}' -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Get-RtdControllerProcess {
    param([Parameter(Mandatory = $true)][string]$Token)

    # The authenticated endpoint identifies our controller. The port and command line
    # are then checked before any process is stopped; PIDs are never persisted.
    if (-not (Test-RtdController -Token $Token)) {
        return $null
    }
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $listener) {
        return $null
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" `
        -ErrorAction SilentlyContinue
    if ($null -eq $process -or $process.CommandLine -notmatch "(?i)-m\s+app\.rtd_control_server") {
        return $null
    }
    $expectedPython = [regex]::Escape($PythonPath)
    $venvPattern = '(?i)^\s*"?' + $expectedPython + '"?\s+.*-m\s+app\.rtd_control_server'
    $directVenvCommand = $process.CommandLine -match $venvPattern
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ParentProcessId)" `
        -ErrorAction SilentlyContinue
    $parentVenvCommand = $null -ne $parent -and $parent.CommandLine -match $venvPattern
    if (-not ($directVenvCommand -or $parentVenvCommand)) {
        return $null
    }
    return $process
}

function Stop-RtdController {
    param([Parameter(Mandatory = $true)][string]$Token)

    $process = Get-RtdControllerProcess -Token $Token
    if ($null -eq $process) {
        return $false
    }
    [void](Stop-RtdCollector -Token $Token)
    Stop-Process -Id $process.ProcessId -ErrorAction Stop
    return $true
}

function Wait-ForDocker {
    param([Parameter(Mandatory = $true)][string]$DockerPath, [int]$TimeoutSeconds = 300)

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        & $DockerPath version --format '{{.Server.Version}}' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Seconds 5
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "O Docker Desktop nao ficou disponivel em $TimeoutSeconds segundos."
}
