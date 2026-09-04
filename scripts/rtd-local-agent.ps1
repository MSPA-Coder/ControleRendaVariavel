<#
.SYNOPSIS
  Instala a tarefa Windows que coleta RTD no PostgreSQL local.

.DESCRIPTION
  O coletor local executa o comando ``poll-rtd --watch`` no mesmo ambiente
  Python isolado usado pelo projeto. Excel/COM permanece no Windows; o
  processo grava no banco local configurado pela aplicação e não abre uma
  porta HTTP nem recebe credenciais por argumento.

  A tarefa é única por usuário e usa um token interativo. Ela inicia no
  logon do Windows e termina quando essa sessão do Windows é encerrada. Isso
  não é o login/logout da sessão web: o coletor é um recurso global da
  máquina, não de uma aba ou de uma requisição Flask.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Uninstall", "Status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

$taskName = "ControleRendaVariavel Coletor Local"
$remoteTaskName = "ControleRendaVariavel Coletor Remoto"
$runnerPath = Join-Path $PSScriptRoot "rtd-local-collector-run.ps1"
$taskPowerShellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Get-LocalCollectorTask {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Get-RemoteCollectorTask {
    Get-ScheduledTask -TaskName $remoteTaskName -ErrorAction SilentlyContinue
}

function Invoke-WithCollectorInstallLock {
    param([scriptblock]$Action)

    $mutex = [Threading.Mutex]::new($false, "Global\ControleRendaVariavelCollectorInstall")
    $acquired = $false
    try {
        $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds(30))
        if (-not $acquired) {
            throw "Não foi possível obter o bloqueio para instalar o coletor; tente novamente."
        }
        & $Action
    } finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

switch ($Action) {
    "Install" {
        Invoke-WithCollectorInstallLock {
            if (-not (Test-Path -LiteralPath $PythonPath)) {
                throw "Ambiente Python do projeto não encontrado em $PythonPath."
            }
            if (-not (Test-Path -LiteralPath $runnerPath)) {
                throw "Inicializador local não encontrado em $runnerPath."
            }
            if (-not (Test-Path -LiteralPath $taskPowerShellPath)) {
                throw "PowerShell do Windows não encontrado em $taskPowerShellPath."
            }
            $remoteTask = Get-RemoteCollectorTask
            if ($null -ne $remoteTask -and $remoteTask.State -ne "Disabled") {
                throw "O agente remoto está instalado e habilitado; remova-o ou desabilite-o antes de instalar o coletor local."
            }

            $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            $scheduledAction = New-ScheduledTaskAction -Execute $taskPowerShellPath `
                -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runnerPath`"" `
                -WorkingDirectory $ProjectDir
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
            $principal = New-ScheduledTaskPrincipal -UserId $identity `
                -LogonType Interactive -RunLevel Limited
            $settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
                -StartWhenAvailable

            if ($PSCmdlet.ShouldProcess($taskName, "registrar coletor local no logon")) {
                Register-ScheduledTask -TaskName $taskName -Action $scheduledAction `
                    -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
                Start-ScheduledTask -TaskName $taskName
                Write-Output "enabled"
            }
        }
    }
    "Uninstall" {
        Invoke-WithCollectorInstallLock {
            if ($PSCmdlet.ShouldProcess($taskName, "remover coletor local")) {
                $task = Get-LocalCollectorTask
                if ($null -ne $task) {
                    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
                    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
                }
                Write-Output "absent"
            }
        }
    }
    "Status" {
        $task = Get-LocalCollectorTask
        if ($null -eq $task) {
            Write-Output "absent"
        } elseif ($task.State -eq "Disabled") {
            Write-Output "disabled"
        } else {
            Write-Output "enabled"
        }
    }
}
