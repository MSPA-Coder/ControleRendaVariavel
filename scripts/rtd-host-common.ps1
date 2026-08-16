Set-StrictMode -Version Latest

# Ligar o Docker, resolver .env e falar com o controlador RTD sao
# responsabilidades de app.host_bootstrap/app.host_env/app.rtd_control_server
# agora -- o unico papel que sobra em PowerShell e o registro da tarefa
# agendada (scripts/rtd-host.ps1) e o wrapper fino que ela executa
# (scripts/rtd-production-runner.ps1), entao so as duas variaveis usadas por
# eles continuam aqui.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectDir ".venv\Scripts\python.exe"
