<#
.SYNOPSIS
  Alvo da tarefa agendada no logon: mantem o controlador RTD residente.

.DESCRIPTION
  Todo o trabalho -- esperar o Docker, aplicar .env, escrever o token do
  compose, subir a pilha como rede de seguranca -- acontece dentro de
  app.rtd_control_server.main() antes de servir. O perfil operacional
  (test/production) so decide, de dentro do processo, se o coletor
  auto-inicia supervisionado; este script roda igual nos dois casos.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Ambiente Python nao encontrado em $PythonPath."
}

& $PythonPath -m app.rtd_control_server
if ($LASTEXITCODE -ne 0) {
    throw "O controlador RTD foi encerrado com codigo $LASTEXITCODE."
}
