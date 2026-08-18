Set-StrictMode -Version Latest

# Ligar o Docker, resolver .env e falar com o controlador RTD sao
# responsabilidades de app.host_bootstrap/app.host_env/app.rtd_control_server
# agora -- o unico papel que sobra em PowerShell e o registro da tarefa
# agendada (scripts/rtd-host.ps1). O controlador Python e iniciado diretamente
# por ``pythonw.exe``, sem uma janela de terminal para a operação depender dela.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PythonWindowlessPath = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
