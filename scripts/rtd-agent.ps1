<#
.SYNOPSIS
  Instala a tarefa Windows única que coleta cotações do ProfitChart.

.DESCRIPTION
  Uma tarefa, um processo, um destino por vez. Para onde as cotações vão --
  o VPS por HTTPS ou o PostgreSQL desta máquina -- é escolhido na tela de
  Configurações, e o coletor passa a obedecer sem reinstalar nada.

  Antes existiam duas tarefas, uma por destino, e a exclusão entre elas era
  mantida por scripts que checavam a existência da outra. Com uma tarefa só,
  a exclusão deixa de ser uma regra a lembrar e passa a ser estrutural.

  A tarefa usa um token interativo e inicia no logon do Windows, com um
  segundo gatilho diário antes do pregão: só o logon deixava a coleta refém
  de um único evento -- uma sessão já aberta, um retorno de hibernação ou a
  inicialização rápida do Windows não contam como logon novo, e o dia
  inteiro passava sem cotação em silêncio.

.PARAMETER ApiUrl
  URL HTTPS do Controle de Renda Variável no VPS. Necessária apenas para o
  destino remoto; sem ela a tarefa é instalada do mesmo jeito e o destino
  remoto falha com uma mensagem no log até ser configurado.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Uninstall", "Status")]
    [string]$Action,
    [string]$ApiUrl
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

$taskName = "ControleRendaVariavel Coletor"
# Tarefas das versoes anteriores, uma por destino. Instalar a unificada
# remove as duas: deixa-las para tras significaria tres coletores possiveis
# disputando a mesma sessao COM.
$legacyTaskNames = @(
    "ControleRendaVariavel Coletor Local",
    "ControleRendaVariavel Coletor Remoto"
)
$runnerPath = Join-Path $PSScriptRoot "rtd-agent-run.ps1"
$configDir = Join-Path $ProjectDir ".docker-local"
$configPath = Join-Path $configDir "remote-collector.env"
$taskPowerShellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Get-CollectorTask {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Stop-ProjectCollectorProcesses {
    <#
      Parar a tarefa nao basta: o Agendador encerra a acao registrada -- o
      powershell.exe -- e o Python que ele iniciou sobrevive, continua
      segurando o lock interprocesso e o novo coletor fica esperando para
      sempre por ele. Alcance limitado a este diretorio de projeto, para nao
      atingir outro checkout na mesma maquina.
    #>
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $null -ne $_.CommandLine -and
            $_.CommandLine.Contains($ProjectDir) -and
            ($_.CommandLine -like "*poll-rtd*" -or $_.CommandLine -like "*app.remote_collector_agent*")
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Verbose "Coletor anterior encerrado: $($_.ProcessId)"
        }
}

function Remove-LegacyCollectorTasks {
    foreach ($name in $legacyTaskNames) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($null -ne $task) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Verbose "Tarefa anterior removida: $name"
        }
    }
    Stop-ProjectCollectorProcesses
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
                throw "Inicializador do coletor não encontrado em $runnerPath."
            }
            # O caminho do Agendador, e nao o do shell que rodou este script:
            # um PowerShell embutido em outra ferramenta nao existe para a
            # Scheduled Task, e a tarefa falharia com "arquivo nao encontrado".
            if (-not (Test-Path -LiteralPath $taskPowerShellPath)) {
                throw "PowerShell do Windows não encontrado em $taskPowerShellPath."
            }
            if (-not [string]::IsNullOrWhiteSpace($ApiUrl)) {
                if (-not $ApiUrl.StartsWith("https://")) {
                    throw "Informe -ApiUrl com a URL HTTPS do Controle de Renda Variável no VPS."
                }
                New-Item -ItemType Directory -Path $configDir -Force | Out-Null
                [System.IO.File]::WriteAllText(
                    $configPath,
                    "COLLECTOR_REMOTE_URL=$($ApiUrl.TrimEnd('/'))`nCOLLECTOR_AGENT_TOKEN_FILE=.secrets/collector_agent_token`n",
                    [System.Text.UTF8Encoding]::new($false)
                )
            }

            $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            $scheduledAction = New-ScheduledTaskAction -Execute $taskPowerShellPath `
                -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runnerPath`"" `
                -WorkingDirectory $ProjectDir
            $trigger = @(
                New-ScheduledTaskTrigger -AtLogOn -User $identity
                New-ScheduledTaskTrigger -Daily -At "09:40"
            )
            $principal = New-ScheduledTaskPrincipal -UserId $identity `
                -LogonType Interactive -RunLevel Limited
            $settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
                -StartWhenAvailable

            if ($PSCmdlet.ShouldProcess($taskName, "registrar coletor no logon")) {
                Remove-LegacyCollectorTasks
                Register-ScheduledTask -TaskName $taskName -Action $scheduledAction `
                    -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
                Start-ScheduledTask -TaskName $taskName
                Write-Output "enabled"
            }
        }
    }
    "Uninstall" {
        Invoke-WithCollectorInstallLock {
            if ($PSCmdlet.ShouldProcess($taskName, "remover coletor")) {
                $task = Get-CollectorTask
                if ($null -ne $task) {
                    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
                    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
                }
                # Remove tambem as tarefas antigas e encerra qualquer coletor
                # que tenha sobrevivido ao Agendador.
                Remove-LegacyCollectorTasks
                Write-Output "absent"
            }
        }
    }
    "Status" {
        $task = Get-CollectorTask
        if ($null -eq $task) {
            Write-Output "absent"
        } elseif ($task.State -eq "Disabled") {
            Write-Output "disabled"
        } else {
            Write-Output "enabled"
        }
    }
}
