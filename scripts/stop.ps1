$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "rtd-host-common.ps1")

$dockerPath = Resolve-DockerCli
& $dockerPath compose --project-directory $ProjectDir down
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao desligar a pilha Docker."
}

if (Test-Path -LiteralPath $TokenPath) {
    $token = (Get-Content -Raw -LiteralPath $TokenPath).Trim()
    if (-not (Stop-RtdController -Token $token)) {
        Write-Warning "O controlador RTD nao foi encerrado: endpoint autenticado ou processo validado indisponivel."
    }
}
