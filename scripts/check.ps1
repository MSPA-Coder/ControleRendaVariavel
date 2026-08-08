<#
.SYNOPSIS
    Executa os controles de qualidade no nivel proporcional a mudanca.

.DESCRIPTION
    A validacao e organizada em aneis. Cada anel custa mais e protege mais,
    e o anel escolhido depende do momento e do que foi alterado -- ver a
    secao "Estrategia de validacao progressiva" do AGENTS.md.

        quick   ruff + testes unitarios                         ~15s
        commit  quick, mais a suite completa quando a mudanca
                alcanca schema, dependencias ou configuracao    ~15s a 35s
        push    suite completa em paralelo + ruff + mypy        ~80s
        all     push, mais auditoria de dependencias, imagem
                de producao e smoke da pilha                    ~3min

    Tudo roda em contêiner: o host so orquestra. As imagens copiam o codigo,
    entao o script reconstroi antes de testar -- sem isso a execucao valida a
    versao anterior.

.PARAMETER Level
    quick, commit, push ou all. Padrao: commit.

.PARAMETER Workers
    Processos paralelos do pytest na suite completa. Padrao: 4.

.EXAMPLE
    .\scripts\check.ps1 quick
    .\scripts\check.ps1 push
#>
[CmdletBinding()]
param(
    [ValidateSet('quick', 'commit', 'push', 'all')]
    [string]$Level = 'commit',

    [ValidateRange(1, 16)]
    [int]$Workers = 4
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)

    Write-Host ""
    Write-Host "-> $Name" -ForegroundColor Cyan
    $started = Get-Date
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FALHOU: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    $seconds = [int]((Get-Date) - $started).TotalSeconds
    Write-Host "   ok (${seconds}s)" -ForegroundColor DarkGray
}

function Get-ChangedPaths {
    # No pre-commit interessa o que esta em stage; nos demais niveis, tudo o
    # que difere de origin/main, para nao perder mudanca ja commitada.
    if ($Level -eq 'commit') {
        $paths = git diff --cached --name-only
    }
    else {
        $paths = git diff --name-only 'origin/main...HEAD'
        $paths += git diff --name-only
    }
    return $paths | Where-Object { $_ }
}

# Mudancas que alcancam schema, dependencias ou a propria infraestrutura de
# teste nao tem "raio pequeno": elas podem quebrar qualquer coisa, entao o
# commit ja paga a suite completa.
$wideReach = @(
    'app/models.py',
    'migrations/',
    'pyproject.toml',
    'compose.yaml',
    'Dockerfile',
    'tests/conftest.py'
)

function Test-PathTouched {
    param([string[]]$Patterns)

    foreach ($path in Get-ChangedPaths) {
        foreach ($pattern in $Patterns) {
            if ($path -like "$pattern*") { return $true }
        }
    }
    return $false
}

function Test-WideReach {
    $changed = Get-ChangedPaths
    foreach ($path in $changed) {
        foreach ($pattern in $wideReach) {
            if ($path -like "$pattern*") {
                Write-Host "   raio amplo: $path" -ForegroundColor Yellow
                return $true
            }
        }
    }
    return $false
}

Write-Host "Validacao nivel '$Level'" -ForegroundColor White

Invoke-Step 'build das imagens' {
    docker compose --profile test build test quality | Out-Null
}

Invoke-Step 'ruff' {
    docker compose --profile test run --rm quality ruff check .
}

if ($Level -eq 'quick') {
    Invoke-Step 'testes unitarios' {
        docker compose --profile test run --rm test pytest -q tests/unit
    }
    Write-Host ""
    Write-Host "OK. Antes do push, rode: .\scripts\check.ps1 push" -ForegroundColor Green
    exit 0
}

if ($Level -eq 'commit') {
    if (Test-WideReach) {
        Invoke-Step 'suite completa (mudanca de raio amplo)' {
            docker compose --profile test run --rm test pytest -q -n $Workers
        }
    }
    else {
        $selection = @('tests/unit')
        # Templates e estaticos nao sao exercitados por teste unitario nenhum:
        # sem isto, uma mudanca de marcacao so seria vista no anel de push.
        if (Test-PathTouched @('app/templates/', 'app/static/')) {
            $selection = @('-m', 'smoke')
        }
        # Autorizacao, CSRF e sessao carregam risco proprio, independentemente
        # do tamanho da mudanca.
        Invoke-Step "testes do anel 2 ($($selection -join ' '))" {
            docker compose --profile test run --rm test pytest -q @selection
        }
        if (Test-PathTouched @('app/routes/auth.py', 'app/__init__.py')) {
            Invoke-Step 'controles de seguranca' {
                docker compose --profile test run --rm test pytest -q -m security
            }
        }
    }
    Write-Host ""
    Write-Host "OK. Antes do push, rode: .\scripts\check.ps1 push" -ForegroundColor Green
    exit 0
}

# push e all
Invoke-Step 'mypy' {
    docker compose --profile test run --rm quality mypy app
}

Invoke-Step 'suite completa' {
    docker compose --profile test run --rm test pytest -q -n $Workers
}

if ($Level -eq 'push') {
    Write-Host ""
    Write-Host "OK para push." -ForegroundColor Green
    exit 0
}

Invoke-Step 'auditoria de dependencias' {
    docker compose --profile test run --rm quality pip-audit
}

Invoke-Step 'cobertura' {
    docker compose --profile test run --rm test
}

Invoke-Step 'imagem de producao e smoke' {
    docker compose up --build -d | Out-Null
    $ok = $false
    foreach ($attempt in 1..30) {
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:5003/health' -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) { $ok = $true; break }
        }
        catch { Start-Sleep -Seconds 2 }
    }
    if (-not $ok) {
        docker compose logs --no-color --tail 50
        $global:LASTEXITCODE = 1
        return
    }
    $global:LASTEXITCODE = 0
}

Write-Host ""
Write-Host "Validacao completa concluida." -ForegroundColor Green
