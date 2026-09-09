<# Coleta local sob demanda. Start cria um processo; Stop o encerra (com a sessao RTD). #>
[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory=$true)][ValidateSet("Start", "Stop", "Status", "Shortcuts")][string]$Action)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")
$taskName = "ControleRendaVariavel Coletor Local"
switch ($Action) {
    "Start" {
        if ($PSCmdlet.ShouldProcess($taskName, "iniciar coleta local")) {
            $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if ($null -eq $task -or $task.State -ne "Running") {
                Register-CollectorTask -Name $taskName -Destination local
                Start-ScheduledTask -TaskName $taskName
            }
            Get-CollectorStatus -Name $taskName
        }
    }
    "Stop" {
        if ($PSCmdlet.ShouldProcess($taskName, "encerrar a coleta local")) {
            Stop-CollectorTask -Name $taskName -Destination local
            Get-CollectorStatus -Name $taskName
        }
    }
    "Shortcuts" {
        $directory = Join-Path $ProjectDir ".docker-local"
        if ($PSCmdlet.ShouldProcess($directory, "criar atalhos de coleta local")) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            $shell = New-Object -ComObject WScript.Shell
            try {
                foreach ($item in @(@{Name="Iniciar coleta local"; Action="Start"}, @{Name="Parar coleta local"; Action="Stop"})) {
                    $shortcut = $shell.CreateShortcut((Join-Path $directory ($item.Name + ".lnk")))
                    $shortcut.TargetPath = Join-Path $env:SystemRoot "System32\conhost.exe"
                    $shortcut.Arguments = '--headless "{0}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{1}" -Action {2}' -f (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"), $PSCommandPath, $item.Action
                    $shortcut.WorkingDirectory = $ProjectDir
                    $shortcut.Description = $item.Name
                    $shortcut.Save()
                    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
                }
            } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) }
        }
    }
    "Status" { Get-CollectorStatus -Name $taskName }
}
