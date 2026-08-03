<#
.SYNOPSIS
  Instala ou desabilita a automacao RTD de producao para o usuario atual.

.DESCRIPTION
  Interface para a aplicacao: .\scripts\rtd-automation.ps1 -Action Enable|Disable|Status
  Enable e idempotente: registra uma tarefa no logon do usuario, sem elevacao,
  com reinicio apos falha. Disable para e desabilita somente essa tarefa; nao
  toca em processos fora dela nem derruba os conteineres.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Enable", "Disable", "Status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

$taskName = "ControleRendaVariavel RTD Production"
$runnerPath = Join-Path $PSScriptRoot "rtd-production-runner.ps1"

function Get-RtdAutomationTask {
    return Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Install-RtdAutomationTask {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`""
    $scheduledAction = New-ScheduledTaskAction -Execute "$PSHOME\powershell.exe" -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $scheduledAction -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
}

switch ($Action) {
    "Enable" {
        Install-RtdAutomationTask
        Enable-ScheduledTask -TaskName $taskName | Out-Null
        $task = Get-RtdAutomationTask
        if ($null -eq $task) {
            throw "A tarefa agendada RTD nao foi criada."
        }
        Write-Output "enabled"
    }
    "Disable" {
        $task = Get-RtdAutomationTask
        if ($null -ne $task) {
            Disable-ScheduledTask -TaskName $taskName | Out-Null
        }
        Write-Output "disabled"
    }
    "Status" {
        $task = Get-RtdAutomationTask
        if ($null -eq $task) {
            Write-Output "absent"
        } elseif ($task.State -eq "Disabled") {
            Write-Output "disabled"
        } else {
            Write-Output "enabled"
        }
    }
}
