$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectDir ".docker-local"
$tokenPath = Join-Path $runtimeDir "rtd-control-token"
$pidPath = Join-Path $runtimeDir "rtd-control.pid"
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

$dockerPath = Resolve-DockerCli
& $dockerPath compose --project-directory $projectDir down
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao desligar a pilha Docker."
}

if (Test-Path -LiteralPath $pidPath) {
    $controllerPid = [int](Get-Content -Raw -LiteralPath $pidPath)
    $controllerProcess = Get-Process -Id $controllerPid -ErrorAction SilentlyContinue
    if ($null -ne $controllerProcess -and $controllerProcess.Path -eq $pythonPath -and `
        (Test-Path -LiteralPath $tokenPath)) {
        $token = (Get-Content -Raw -LiteralPath $tokenPath).Trim()
        $headers = @{ Authorization = "Bearer $token" }
        $body = '{"enabled":false}'
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:8765/state" `
                -Method Post `
                -Headers $headers `
                -ContentType "application/json" `
                -Body $body | Out-Null
        } catch {
            Write-Warning "Não foi possível solicitar o desligamento do coletor RTD."
        }
    }
    if ($null -ne $controllerProcess -and $controllerProcess.Path -eq $pythonPath) {
        Stop-Process -Id $controllerPid
    } elseif ($null -ne $controllerProcess) {
        Write-Warning "PID salvo pertence a outro processo; ele não será encerrado."
    }
    Remove-Item -LiteralPath $pidPath -Force
}
