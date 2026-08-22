Set-StrictMode -Version Latest

# Caminhos usados pelo agente RTD agendado. O agente Python é iniciado por
# ``pythonw.exe`` sem abrir uma janela de terminal.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PythonWindowlessPath = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
