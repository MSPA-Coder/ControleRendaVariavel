[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Ambiente Python nao encontrado em $PythonPath."
}
$dockerPath = Resolve-DockerCli
$token = Get-RtdControlToken
Set-RtdControlComposeToken -Token $token
Set-RtdCollectorEnvironment

if (-not (Test-RtdController -Token $token)) {
    $stdoutPath = Join-Path $RuntimeDir "rtd-control.stdout.log"
    $stderrPath = Join-Path $RuntimeDir "rtd-control.stderr.log"
    $controller = Start-Process -FilePath $PythonPath -ArgumentList "-m", "app.rtd_control_server" `
        -WorkingDirectory $ProjectDir -WindowStyle Hidden -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if ($controller.HasExited) {
            $details = if (Test-Path -LiteralPath $stderrPath) {
                (Get-Content -Raw -LiteralPath $stderrPath).Trim()
            } else { "" }
            throw "O controlador RTD encerrou durante a inicializacao. $details"
        }
        if (Test-RtdController -Token $token) {
            break
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not (Test-RtdController -Token $token)) {
        throw "O controlador RTD nao respondeu na porta 8765 em ate 10 segundos."
    }
}

& $dockerPath compose --project-directory $ProjectDir up --build -d
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao construir ou iniciar a pilha Docker."
}
Write-Output "Aplicacao disponivel em http://127.0.0.1:5003"
