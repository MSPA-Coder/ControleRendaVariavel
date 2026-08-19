<#
.SYNOPSIS
  Instala a tarefa Windows que envia cotações RTD ao VPS por HTTPS.

.DESCRIPTION
  O agente não abre portas no Windows. Ele lê a URL pública e o arquivo de
  token de .docker-local\remote-collector.env. O intervalo de verificação e
  a agenda são recebidos da tela Configurações e guardados localmente.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Uninstall", "Status")]
    [string]$Action,
    [string]$ApiUrl
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

$taskName = "ControleRendaVariavel Coletor Remoto"
$configDir = Join-Path $ProjectDir ".docker-local"
$configPath = Join-Path $configDir "remote-collector.env"

function Get-RemoteCollectorTask {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

switch ($Action) {
    "Install" {
        if ([string]::IsNullOrWhiteSpace($ApiUrl) -or -not $ApiUrl.StartsWith("https://")) {
            throw "Informe -ApiUrl com a URL HTTPS do Controle de Renda Variável no VPS."
        }
        if (-not (Test-Path -LiteralPath $PythonWindowlessPath)) {
            throw "Ambiente Python sem janela não encontrado em $PythonWindowlessPath."
        }
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
        [System.IO.File]::WriteAllText(
            $configPath,
            "COLLECTOR_REMOTE_URL=$($ApiUrl.TrimEnd('/'))`nCOLLECTOR_AGENT_TOKEN_FILE=.secrets/collector_agent_token`n",
            [System.Text.UTF8Encoding]::new($false)
        )
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $scheduledAction = New-ScheduledTaskAction -Execute $PythonWindowlessPath `
            -Argument "-m app.remote_collector_agent" -WorkingDirectory $ProjectDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $taskName -Action $scheduledAction -Trigger $trigger `
            -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Write-Output "enabled"
    }
    "Uninstall" {
        $task = Get-RemoteCollectorTask
        if ($null -ne $task) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
        Write-Output "absent"
    }
    "Status" {
        $task = Get-RemoteCollectorTask
        if ($null -eq $task) { Write-Output "absent" } elseif ($task.State -eq "Disabled") { Write-Output "disabled" } else { Write-Output "enabled" }
    }
}
