$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$projectItem = Get-Item -LiteralPath $projectDir
if ($projectItem.LinkType -eq "Junction" -and $projectItem.Target) {
    $projectDir = $projectItem.Target[0]
}
$runtimeDir = Join-Path $projectDir ".docker-local"
$tokenPath = Join-Path $runtimeDir "rtd-control-token"
$pidPath = Join-Path $runtimeDir "rtd-control.pid"
$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Ambiente Python não encontrado em $pythonPath."
}
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

$controllerRunning = $false
if (Test-Path -LiteralPath $pidPath) {
    $controllerPid = [int](Get-Content -Raw -LiteralPath $pidPath)
    $existingProcess = Get-Process -Id $controllerPid -ErrorAction SilentlyContinue
    $controllerRunning = $null -ne $existingProcess -and `
        $existingProcess.Path -eq $pythonPath
}

$env:RTD_CONTROL_TOKEN = $token
if (-not $controllerRunning) {
    $controller = Start-Process -FilePath $pythonPath `
        -ArgumentList "-m", "app.rtd_control_server" `
        -WorkingDirectory $projectDir `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $controller.Id -NoNewline
}

docker compose --project-directory $projectDir up --build -d
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao construir ou iniciar a pilha Docker."
}
Write-Output "Aplicação disponível em http://127.0.0.1:8000"
