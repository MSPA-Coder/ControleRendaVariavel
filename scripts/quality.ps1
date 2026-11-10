# Verificação completa do repositório, num comando só e uma de cada vez.
#
# Cópia de _manutencao/compartilhados/quality.ps1: mude lá e rode
# `python compartilhados/sincronizar.py --aplicar`. O que muda de um
# repositório para outro fica em `scripts/quality.passos`, uma linha por passo:
#
#   run --build --rm quality     argumentos de `docker compose`
#   prefixo: --profile quality   argumentos postos antes de todo passo do Compose
#   !docker run --rm ...         comando inteiro, como está; `{raiz}` vira a
#                                pasta do checkout
#   # comentário                 ignorado, como a linha vazia
#
# Os passos rodam em ordem e o primeiro que falha encerra. Não há aspas: cada
# espaço separa um argumento.
#
#   .\scripts\quality.ps1                         todos os passos
#   .\scripts\quality.ps1 run --rm quality mypy   um comando só, com o prefixo
#
# O mutex é do host e leva o nome da pasta do checkout: uma segunda chamada
# espera a primeira, em vez de recriar o ambiente de teste debaixo dela. Por
# isso a rodada parcial também passa por aqui.
#
# As mensagens vão sem acento de propósito: o PowerShell 5.1 lê este arquivo
# (UTF-8 sem BOM) como ANSI e as mostraria trocadas.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$pedido = @($args)
$root = Split-Path -Parent $PSScriptRoot
$arquivo = Join-Path $PSScriptRoot 'quality.passos'
$linhas = @(Get-Content -LiteralPath $arquivo -Encoding UTF8 |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith('#') })

$prefixo = @()
$comandos = New-Object System.Collections.ArrayList
foreach ($linha in $linhas) {
    if ($linha.StartsWith('prefixo:')) {
        $prefixo = @($linha.Substring(8).Trim() -split '\s+' | Where-Object { $_ })
    } elseif ($linha.StartsWith('!')) {
        $partes = @($linha.Substring(1).Trim() -split '\s+' | ForEach-Object { $_.Replace('{raiz}', $root) })
        [void]$comandos.Add($partes)
    } else {
        [void]$comandos.Add(@('docker', 'compose') + $prefixo + @($linha -split '\s+'))
    }
}
if ($pedido.Count -gt 0) {
    $comandos.Clear()
    [void]$comandos.Add(@('docker', 'compose') + $prefixo + $pedido)
}
if ($comandos.Count -eq 0) {
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
        foreach ($comando in $comandos) {
            $programa = $comando[0]
            $resto = @($comando | Select-Object -Skip 1)
            Write-Host "==> $($comando -join ' ')"
            & $programa @resto
            $exitCode = $LASTEXITCODE
            if ($exitCode -ne 0) {
                Write-Host "Passo reprovado (exit $exitCode): $($comando -join ' ')"
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
