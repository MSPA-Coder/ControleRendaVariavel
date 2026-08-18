<#
.SYNOPSIS
  Instala, remove ou consulta a tarefa agendada que mantem o controlador RTD
  residente no host.

.DESCRIPTION
  scripts\rtd-host.ps1 -Action Install|Uninstall|Status

  A tarefa sobe no logon do usuario atual, sem elevacao, e fica sempre
  habilitada nos dois perfis operacionais: o perfil (test/production),
  gerenciado pela aba Settings da aplicacao, so decide se o coletor
  auto-inicia supervisionado -- nao se a tarefa existe. Isto e so o
  instalador; a operacao de fato (Docker, .env, servidor de controle) esta
  em app.rtd_control_server, iniciado diretamente sem janela de terminal.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Uninstall", "Status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

$taskName = "ControleRendaVariavel RTD"

function Get-RtdHostTask {
    return Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

switch ($Action) {
    "Install" {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        if (-not (Test-Path -LiteralPath $PythonWindowlessPath)) {
            throw "Ambiente Python sem janela nao encontrado em $PythonWindowlessPath."
        }
        # A tarefa não passa por PowerShell nem abre terminal. ``pythonw.exe``
        # mantém o controlador na sessão interativa necessária ao COM/Profit,
        # enquanto o Agendador trata as reinicializações automáticas.
        $scheduledAction = New-ScheduledTaskAction -Execute $PythonWindowlessPath `
            -Argument "-m app.rtd_control_server" -WorkingDirectory $ProjectDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $taskName -Action $scheduledAction -Trigger $trigger `
            -Principal $principal -Settings $settings -Force | Out-Null
        Enable-ScheduledTask -TaskName $taskName | Out-Null
        if ($null -eq (Get-RtdHostTask)) {
            throw "A tarefa agendada RTD nao foi criada."
        }
        Write-Output "enabled"
    }
    "Uninstall" {
        $task = Get-RtdHostTask
        if ($null -ne $task) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
        Write-Output "absent"
    }
    "Status" {
        $task = Get-RtdHostTask
        if ($null -eq $task) {
            Write-Output "absent"
        } elseif ($task.State -eq "Disabled") {
            Write-Output "disabled"
        } else {
            Write-Output "enabled"
        }
    }
}
