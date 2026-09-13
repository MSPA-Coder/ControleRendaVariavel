Set-StrictMode -Version Latest
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectDir ".venv\Scripts\python.exe"

function Register-CollectorTask {
    param([string]$Name, [ValidateSet("remote", "local")][string]$Destination)
    if (-not (Test-Path -LiteralPath $PythonPath)) { throw "Ambiente RTD ausente: $PythonPath" }
    $runnerPath = Join-Path $ProjectDir "scripts\rtd-agent-run.ps1"
    $shellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $conhostPath = Join-Path $env:SystemRoot "System32\conhost.exe"
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $arguments = '--headless "{0}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{1}" -Destination {2}' -f $shellPath, $runnerPath, $Destination
    $action = New-ScheduledTaskAction -Execute $conhostPath -Argument $arguments -WorkingDirectory $ProjectDir
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $options = @{
        AllowStartIfOnBatteries = $true
        DontStopIfGoingOnBatteries = $true
        ExecutionTimeLimit = [TimeSpan]::Zero
        MultipleInstances = 'IgnoreNew'
    }
    $parameters = @{TaskName=$Name; Action=$action; Principal=$principal; Force=$true}
    if ($Destination -eq "remote") {
        $options.RestartCount = 999
        $options.RestartInterval = New-TimeSpan -Minutes 1
        $options.StartWhenAvailable = $true
        $parameters.Trigger = @(
            New-ScheduledTaskTrigger -AtLogOn -User $identity
            New-ScheduledTaskTrigger -Daily -At "09:40"
        )
    }
    # Local: sem gatilhos e sem reinício automático. Parado é zero processos.
    $parameters.Settings = New-ScheduledTaskSettingsSet @options
    Register-ScheduledTask @parameters | Out-Null
}

function Stop-CollectorTask {
    param([string]$Name, [ValidateSet("remote", "local")][string]$Destination)
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) { return }
    # Aguarda também a inicialização: Stop pode chegar antes de o Python criar
    # seu evento. Só este comando temporário consulta o estado, nunca um vigia.
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ($task.State -eq "Running") {
        Push-Location $ProjectDir
        try { & $PythonPath -m app.collector.control $Destination | Out-Null }
        finally { Pop-Location }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "O coletor não encerrou em 45s; confira o log. O processo não foi encerrado à força."
        }
        Start-Sleep -Milliseconds 250
        $task = Get-ScheduledTask -TaskName $Name
    }
}

function Get-CollectorStatus {
    param([string]$Name)
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) { Write-Output "absent"; return }
    $info = $task | Get-ScheduledTaskInfo
    [pscustomobject]@{Task=$Name; State=$task.State; LastRun=$info.LastRunTime; Result=$info.LastTaskResult}
}
