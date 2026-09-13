<# Inicializador das tarefas RTD. Segredos nunca entram nos argumentos ou logs. #>
param([ValidateSet("remote", "local")][string]$Destination = "remote")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")
Set-Location $ProjectDir

if ($Destination -eq "local") {
    $env:FLASK_SKIP_DOTENV = "1"
    Remove-Item Env:DATABASE_URL, Env:DATABASE_URL_FILE -ErrorAction SilentlyContinue
    $env:SECRET_KEY_FILE = Join-Path $ProjectDir ".secrets\secret_key"
    $env:POSTGRES_PASSWORD_FILE = Join-Path $ProjectDir ".secrets\postgres_password"
    $env:POSTGRES_HOST = "127.0.0.1"
    $env:POSTGRES_PORT = "5302"
    $env:POSTGRES_DB = "investimentos"
    $env:POSTGRES_USER = "investimentos"
    $env:REMOTE_COLLECTOR_ENABLED = "false"
    # Limites só do agente local: a queda do Docker não pode prender Stop
    # durante minutos dentro de uma conexão ou consulta PostgreSQL.
    $env:PGCONNECT_TIMEOUT = "5"
    $env:PGOPTIONS = "-c statement_timeout=10000 -c lock_timeout=5000"
    $pythonArguments = @("-m", "flask", "--app", "app:create_app", "poll-rtd", "--watch")
} else {
    # Não carrega .env nem os arquivos de segredo do PostgreSQL/Flask.
    $pythonArguments = @("-m", "app.collector.remote_agent")
}
$logDir = Join-Path $env:LOCALAPPDATA "ControleRendaVariavel"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir ("{0}-runner.log" -f $Destination)
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$utf8SemBom = [System.Text.UTF8Encoding]::new($false)
# PowerShell 5.1 representa stderr como ErrorRecord; os avisos não podem matar
# o pipeline. O código final do Python é devolvido ao Agendador, inclusive falhas.
$ErrorActionPreference = "Continue"
& $PythonPath @pythonArguments 2>&1 | ForEach-Object {
    [System.IO.File]::AppendAllText($logPath, $_.ToString() + [Environment]::NewLine, $utf8SemBom)
}
exit $LASTEXITCODE
