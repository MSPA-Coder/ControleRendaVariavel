<# Instala o agente automático exclusivo do VPS; não depende do banco local. #>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory=$true)][ValidateSet("Install", "Uninstall", "Status")][string]$Action,
    [string]$ApiUrl
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")
$taskName = "ControleRendaVariavel Coletor Remoto"
$configPath = Join-Path $ProjectDir ".docker-local\remote-collector.env"

function Remove-UnifiedCollector {
    $legacyName = "ControleRendaVariavel Coletor"
    if ($null -eq (Get-ScheduledTask -TaskName $legacyName -ErrorAction SilentlyContinue)) { return }
    # Descendentes do runner legado, capturados antes de parar a tarefa.
    # Não atinge um coletor local novo que já esteja ativo em paralelo.
    $processes = @(Get-CimInstance Win32_Process)
    $legacyRunners = @($processes | Where-Object {
        $_.Name -eq 'powershell.exe' -and $_.CommandLine -and
        $_.CommandLine.Contains($ProjectDir) -and $_.CommandLine -like '*rtd-agent-run.ps1*' -and
        $_.CommandLine -notmatch '-Destination\s+(local|remote)'
    } | ForEach-Object { $_.ProcessId })
    $children = @($processes | Where-Object { $_.ParentProcessId -in $legacyRunners -and $_.Name -match '^pythonw?\.exe$' })
    $childIds = @($children | ForEach-Object { $_.ProcessId })
    $descendants = @($processes | Where-Object { $_.ParentProcessId -in $childIds -and $_.Name -match '^pythonw?\.exe$' })
    Stop-ScheduledTask -TaskName $legacyName
    @($descendants) + @($children) | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Unregister-ScheduledTask -TaskName $legacyName -Confirm:$false
}

switch ($Action) {
    "Install" {
        if (-not (Test-Path -LiteralPath $PythonPath)) { throw "Ambiente RTD não encontrado." }
        if ($ApiUrl -and -not $ApiUrl.StartsWith("https://")) { throw "Informe uma URL HTTPS." }
        if (-not $ApiUrl -and -not (Test-Path -LiteralPath $configPath)) { throw "Informe -ApiUrl com a URL do VPS." }
        if ($PSCmdlet.ShouldProcess($taskName, "instalar agente automático para o VPS")) {
            if ($ApiUrl) {
                New-Item -ItemType Directory -Path (Split-Path -Parent $configPath) -Force | Out-Null
                # Preserva opções RTD existentes; substitui somente a URL e o caminho do token.
                $lines = @()
                if (Test-Path -LiteralPath $configPath) {
                    $lines = @(Get-Content -LiteralPath $configPath | Where-Object { $_ -notmatch '^COLLECTOR_REMOTE_URL=|^COLLECTOR_AGENT_TOKEN_FILE=' })
                }
                $lines += "COLLECTOR_REMOTE_URL=$($ApiUrl.TrimEnd('/'))"
                $lines += "COLLECTOR_AGENT_TOKEN_FILE=.secrets/collector_agent_token"
                [IO.File]::WriteAllLines($configPath, [string[]]$lines, [Text.UTF8Encoding]::new($false))
            }
            Stop-CollectorTask -Name $taskName -Destination remote
            Remove-UnifiedCollector
            Register-CollectorTask -Name $taskName -Destination remote
            Start-ScheduledTask -TaskName $taskName
            Get-CollectorStatus -Name $taskName
        }
    }
    "Uninstall" {
        if ($PSCmdlet.ShouldProcess($taskName, "remover agente remoto")) {
            Stop-CollectorTask -Name $taskName -Destination remote
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
            Write-Output "absent"
        }
    }
    "Status" { Get-CollectorStatus -Name $taskName }
}
