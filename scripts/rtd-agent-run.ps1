<#
.SYNOPSIS
  Inicializa o coletor RTD com segredos por arquivo, fora do Docker.

.DESCRIPTION
  Este processo é chamado somente pela Scheduled Task. Os valores sensíveis
  são passados ao filho pelo ambiente do processo, nunca pela linha de
  comando ou pelo log.

  O destino da coleta -- o VPS ou o banco desta máquina -- não é decidido
  aqui: `poll-rtd` o lê da tela de Configurações a cada ciclo. Este script
  só garante que o processo tenha como falar com o banco local, que é onde
  essa escolha está guardada.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

$secretDir = Join-Path $ProjectDir ".secrets"
$secretKeyPath = Join-Path $secretDir "secret_key"
$passwordPath = Join-Path $secretDir "postgres_password"
foreach ($path in @($secretKeyPath, $passwordPath)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Arquivo de segredo exigido pelo coletor não foi encontrado."
    }
}

$env:SECRET_KEY_FILE = $secretKeyPath
$env:POSTGRES_PASSWORD_FILE = $passwordPath
$env:POSTGRES_HOST = "127.0.0.1"
$env:POSTGRES_PORT = "5302"
$env:POSTGRES_DB = "investimentos"
$env:POSTGRES_USER = "investimentos"
$env:REMOTE_COLLECTOR_ENABLED = "false"

$logDir = Join-Path $env:LOCALAPPDATA "ControleRendaVariavel"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir "collector.log"

Set-Location $ProjectDir

# O Windows PowerShell transforma cada linha de stderr de um executavel nativo
# em ErrorRecord; com "Stop" isso encerraria o coletor no primeiro aviso do
# Flask. A partir daqui os erros apenas seguem para o log.
$ErrorActionPreference = "Continue"
& $PythonPath -m flask --app app:create_app poll-rtd --watch 2>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logPath -Append -Encoding utf8
exit $LASTEXITCODE
