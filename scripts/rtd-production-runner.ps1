[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

$profilePath = Join-Path $RuntimeDir "operational-profile"
function Test-ProductionProfile {
    return (Test-Path -LiteralPath $profilePath) -and
        (Get-Content -Raw -LiteralPath $profilePath).Trim() -eq "production"
}

if (-not (Test-ProductionProfile)) {
    Write-Output "Perfil operacional nao e production; o runner RTD nao sera iniciado."
    exit 0
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Ambiente Python nao encontrado em $PythonPath."
}

$dockerPath = Resolve-DockerCli
Wait-ForDocker -DockerPath $dockerPath
$token = Get-RtdControlToken
Set-RtdControlComposeToken -Token $token
& $dockerPath compose --project-directory $ProjectDir up -d
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao iniciar a pilha Docker."
}

Set-RtdCollectorEnvironment

# A task owns this foreground process. If a manual controller is already healthy,
# wait for it to end before taking ownership instead of relying on a saved PID.
while (Test-RtdController -Token $token) {
    if (-not (Test-ProductionProfile)) {
        Write-Output "Perfil operacional mudou para test; o runner RTD sera encerrado."
        exit 0
    }
    Start-Sleep -Seconds 5
}
if (-not (Test-ProductionProfile)) {
    Write-Output "Perfil operacional mudou para test; o runner RTD nao iniciara o controlador."
    exit 0
}

& $PythonPath -m app.rtd_control_server
if ($LASTEXITCODE -ne 0) {
    throw "O controlador RTD de producao foi encerrado com codigo $LASTEXITCODE."
}
throw "O controlador RTD de producao foi encerrado inesperadamente."
