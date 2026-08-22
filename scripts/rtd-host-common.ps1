Set-StrictMode -Version Latest

# Caminhos comuns aos scripts de tarefa agendada. Hoje so
# `rtd-remote-agent.ps1` carrega este arquivo: o modo de controlador local
# saiu em 2026-08-22, e com ele `rtd-host.ps1`. O agente Python e iniciado
# direto por ``pythonw.exe``, sem uma janela de terminal de que a operacao
# dependa.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PythonWindowlessPath = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
