Set-StrictMode -Version Latest

# Caminhos usados pelo agente RTD agendado. Quem evita a janela de terminal é
# o hospedeiro de console que a Scheduled Task escolhe (ver rtd-agent.ps1), e
# não o executável do Python: o coletor precisa do python.exe, cuja saída é
# canalizada para o log.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectDir ".venv\Scripts\python.exe"
