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
$localTaskName = "ControleRendaVariavel Coletor Local"
$configDir = Join-Path $ProjectDir ".docker-local"
$configPath = Join-Path $configDir "remote-collector.env"

function Get-RemoteCollectorTask {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Get-LocalCollectorTask {
    Get-ScheduledTask -TaskName $localTaskName -ErrorAction SilentlyContinue
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
            if ([string]::IsNullOrWhiteSpace($ApiUrl) -or -not $ApiUrl.StartsWith("https://")) {
                throw "Informe -ApiUrl com a URL HTTPS do Controle de Renda Variável no VPS."
            }
            if (-not (Test-Path -LiteralPath $PythonWindowlessPath)) {
                throw "Ambiente Python sem janela não encontrado em $PythonWindowlessPath."
            }
            $localTask = Get-LocalCollectorTask
            if ($null -ne $localTask -and $localTask.State -ne "Disabled") {
                throw "O coletor local está instalado e habilitado; remova-o ou desabilite-o antes de instalar o agente remoto."
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
            # Dois gatilhos, e o segundo existe por experiencia: so o logon deixa a
            # coleta refem de um unico evento. Uma sessao que ja estava aberta, um
            # retorno de hibernacao ou uma inicializacao rapida do Windows nao
            # contam como logon novo, e o dia inteiro passa sem cotacao em silencio.
            # O gatilho diario tenta de novo antes da janela de coleta; se o agente
            # ja estiver rodando, MultipleInstances IgnoreNew descarta a segunda.
            $trigger = @(
                New-ScheduledTaskTrigger -AtLogOn -User $identity
                New-ScheduledTaskTrigger -Daily -At "09:40"
            )
            $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
                -StartWhenAvailable
            Register-ScheduledTask -TaskName $taskName -Action $scheduledAction -Trigger $trigger `
                -Principal $principal -Settings $settings -Force | Out-Null
            Start-ScheduledTask -TaskName $taskName
            Write-Output "enabled"
        }
    }
    "Uninstall" {
        Invoke-WithCollectorInstallLock {
            $task = Get-RemoteCollectorTask
            if ($null -ne $task) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
            Write-Output "absent"
        }
    }
    "Status" {
        $task = Get-RemoteCollectorTask
        if ($null -eq $task) { Write-Output "absent" } elseif ($task.State -eq "Disabled") { Write-Output "disabled" } else { Write-Output "enabled" }
    }
}
