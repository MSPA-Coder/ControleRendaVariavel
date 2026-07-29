$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectDir ".docker-local"
$tokenPath = Join-Path $runtimeDir "rtd-control-token"
$pidPath = Join-Path $runtimeDir "rtd-control.pid"
$stdoutPath = Join-Path $runtimeDir "rtd-control.stdout.log"
$stderrPath = Join-Path $runtimeDir "rtd-control.stderr.log"
$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"

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

function Test-RtdController {
    param([Parameter(Mandatory = $true)][string]$Token)

    try {
        $headers = @{ Authorization = "Bearer $Token" }
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8765/state" `
            -Headers $headers `
            -TimeoutSec 1
        return $null -ne $response.running
    } catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Ambiente Python não encontrado em $pythonPath."
}
$dockerPath = Resolve-DockerCli
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

if (Test-Path -LiteralPath $tokenPath) {
    $token = (Get-Content -Raw -LiteralPath $tokenPath).Trim()
} else {
    $tokenBytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($tokenBytes)
    } finally {
        $random.Dispose()
    }
    $token = [BitConverter]::ToString($tokenBytes).Replace("-", "")
    Set-Content -LiteralPath $tokenPath -Value $token -NoNewline
}
if ($token.Length -lt 32) {
    throw "Token do controlador RTD inválido em $tokenPath."
}

$defaultDatabaseUrl = `
    "postgresql+psycopg://investimentos:investimentos@127.0.0.1:5435/investimentos"
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
    $env:DATABASE_URL = $defaultDatabaseUrl
}

$controllerRunning = $false
$env:RTD_CONTROL_TOKEN = $token
if (Test-RtdController -Token $token) {
    $controllerRunning = $true
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $listener) {
        $listenerProcess = Get-Process -Id $listener.OwningProcess `
            -ErrorAction SilentlyContinue
        if ($null -ne $listenerProcess -and $listenerProcess.Path -eq $pythonPath) {
            Set-Content -LiteralPath $pidPath `
                -Value $listenerProcess.Id `
                -NoNewline
        }
    }
} else {
    if (Test-Path -LiteralPath $pidPath) {
        $controllerPid = [int](Get-Content -Raw -LiteralPath $pidPath)
        $staleProcess = Get-Process -Id $controllerPid -ErrorAction SilentlyContinue
        if ($null -ne $staleProcess -and $staleProcess.Path -eq $pythonPath) {
            Stop-Process -Id $controllerPid -Force
        }
        Remove-Item -LiteralPath $pidPath -Force
    }

    $controller = Start-Process -FilePath $pythonPath `
        -ArgumentList "-m", "app.rtd_control_server" `
        -WorkingDirectory $projectDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if ($controller.HasExited) {
            $details = ""
            if (Test-Path -LiteralPath $stderrPath) {
                $details = (Get-Content -Raw -LiteralPath $stderrPath).Trim()
            }
            throw "O controlador RTD encerrou durante a inicialização. $details"
        }
        if (Test-RtdController -Token $token) {
            $controllerRunning = $true
            break
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)

    if (-not $controllerRunning) {
        Stop-Process -Id $controller.Id -Force -ErrorAction SilentlyContinue
        throw "O controlador RTD não respondeu na porta 8765 em até 10 segundos."
    }
    Set-Content -LiteralPath $pidPath -Value $controller.Id -NoNewline
}

& $dockerPath compose --project-directory $projectDir up --build -d
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao construir ou iniciar a pilha Docker."
}
Write-Output "Aplicação disponível em http://127.0.0.1:5003"
