# Verificação completa do repositório, num comando só e uma de cada vez.
#
# Cópia de _manutencao/compartilhados/quality.ps1: mude lá e rode
# `python compartilhados/sincronizar.py --aplicar`. O que muda de um
# repositório para outro fica em `scripts/quality.passos`: uma linha por passo,
# com os argumentos de `docker compose`; linha vazia ou iniciada por `#` é
# ignorada. Os passos rodam em ordem e o primeiro que falha encerra.
#
#   .\scripts\quality.ps1
#
# O mutex é do host e leva o nome da pasta do checkout: uma segunda chamada
# espera a primeira, em vez de recriar o ambiente de teste debaixo dela. Para
# iterar num arquivo de teste, use direto o comando do passo.
#
# As mensagens vão sem acento de propósito: o PowerShell 5.1 lê este arquivo
# (UTF-8 sem BOM) como ANSI e as mostraria trocadas.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$arquivo = Join-Path $PSScriptRoot 'quality.passos'
$passos = @(Get-Content -LiteralPath $arquivo -Encoding UTF8 |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith('#') })
if ($passos.Count -eq 0) {
    throw "Nenhum passo em $arquivo."
}

$mutex = New-Object System.Threading.Mutex($false, "Global\$(Split-Path -Leaf $root).Quality")
$acquired = $false
$exitCode = 1

try {
    Write-Host 'Aguardando outra chamada deste script terminar, se houver...'
    try {
        $acquired = $mutex.WaitOne([TimeSpan]::FromMinutes(45))
    } catch [System.Threading.AbandonedMutexException] {
        # A execução anterior caiu; o Windows já concedeu o mutex a esta.
        $acquired = $true
        Write-Warning 'A chamada anterior caiu no meio; retomando a trava.'
    }
    if (-not $acquired) {
        throw 'Ocupado por mais de 45 minutos. Confira a chamada anterior antes de tentar de novo.'
    }

    Push-Location $root
    try {
        foreach ($passo in $passos) {
            Write-Host "==> docker compose $passo"
            $argumentos = @($passo -split '\s+')
            & docker compose @argumentos
            $exitCode = $LASTEXITCODE
            if ($exitCode -ne 0) {
                Write-Host "Passo reprovado (exit $exitCode): docker compose $passo"
                break
            }
        }
    } finally {
        Pop-Location
    }
} finally {
    if ($acquired) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}

exit $exitCode
