param(
    [ValidateSet("Demo", "Up", "Down", "Config", "Logs")]
    [string]$Action = "Demo",
    [string]$Question = "脑梗发生后多久内需要尽快就医？",
    [int]$TopK = 5,
    [int]$TimeoutSeconds = 180,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$reportDir = Join-Path $repoRoot "eval\reports"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker compose @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Assert-PathExists {
    param([Parameter(Mandatory = $true)][string]$PathValue, [Parameter(Mandatory = $true)][string]$Label)
    if (-not (Test-Path -LiteralPath $PathValue)) {
        throw "$Label is missing: $PathValue"
    }
}

if (-not $env:BGE_MODEL_HOST_PATH) {
    $env:BGE_MODEL_HOST_PATH = Join-Path $repoRoot "models\bge-m3"
}
Assert-PathExists $env:BGE_MODEL_HOST_PATH "BGE model directory"
if (-not $env:OBSIDIAN_HOST_PATH) {
    $env:OBSIDIAN_HOST_PATH = Join-Path $repoRoot "local-data\vault"
}
Assert-PathExists $env:OBSIDIAN_HOST_PATH "Obsidian vault directory"
Assert-PathExists (Join-Path $repoRoot "index") "Persisted index directory"

switch ($Action) {
    "Config" {
        Invoke-Compose @("config")
        break
    }
    "Up" {
        Invoke-Compose @("up", "-d", "--build")
        Write-Output "Docker demo service started at http://127.0.0.1:8000"
        break
    }
    "Down" {
        Invoke-Compose @("down")
        Write-Output "Docker demo service stopped."
        break
    }
    "Logs" {
        Invoke-Compose @("logs", "--tail", "200", "rag-api")
        break
    }
    "Demo" {
        $startedAt = Get-Date
        $timestamp = $startedAt.ToString("yyyyMMdd-HHmmss")
        $resultPath = Join-Path $reportDir "docker-demo-$timestamp.json"
        $logPath = Join-Path $reportDir "docker-demo-$timestamp-compose.log"
        $health = $null
        $response = $null

        Invoke-Compose @("config")
        try {
            Invoke-Compose @("up", "-d", "--build")
        }
        catch {
            & docker compose ps -a | Out-File -FilePath $logPath -Encoding utf8
            & docker compose logs --tail 200 rag-api | Out-File -FilePath $logPath -Encoding utf8 -Append
            $failureRecord = [ordered]@{
                started_at = $startedAt.ToString("o")
                finished_at = (Get-Date).ToString("o")
                status = "failed"
                failed_stage = "compose_up_build"
                error = $_.Exception.Message
                host_paths = [ordered]@{
                    bge_model = $env:BGE_MODEL_HOST_PATH
                    obsidian_vault = $env:OBSIDIAN_HOST_PATH
                    index = (Join-Path $repoRoot "index")
                }
                compose_log = $logPath
            }
            [IO.File]::WriteAllText($resultPath, ($failureRecord | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding($false)))
            Write-Output "Demo failure record: $resultPath"
            Write-Output "Compose log: $logPath"
            throw
        }

        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            try {
                $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/health" -TimeoutSec 10
                if ($health.service -eq "ready") {
                    break
                }
            }
            catch {
                $health = $null
            }
            Start-Sleep -Seconds 3
        }

        if ($null -eq $health -or $health.service -ne "ready") {
            & docker compose logs --tail 200 rag-api | Out-File -FilePath $logPath -Encoding utf8
            throw "Docker API did not become ready within $TimeoutSeconds seconds. See $logPath"
        }

        $requestBody = @{
            question = $Question
            top_k = $TopK
            filters = @{ domain = "medical" }
            rewrite = $false
            semantic_grounding = $true
        } | ConvertTo-Json -Depth 10

        $response = Invoke-RestMethod -Method Post `
            -Uri "http://127.0.0.1:8000/query" `
            -ContentType "application/json" `
            -Body $requestBody

        & docker compose logs --tail 200 rag-api | Out-File -FilePath $logPath -Encoding utf8
        $composePs = (& docker compose ps) -join [Environment]::NewLine
        $record = [ordered]@{
            started_at = $startedAt.ToString("o")
            finished_at = (Get-Date).ToString("o")
            host_paths = [ordered]@{
                bge_model = $env:BGE_MODEL_HOST_PATH
                obsidian_vault = $env:OBSIDIAN_HOST_PATH
                index = (Join-Path $repoRoot "index")
            }
            compose_ps = $composePs
            health = $health
            request = ($requestBody | ConvertFrom-Json)
            response = $response
            compose_log = $logPath
        }
        [IO.File]::WriteAllText($resultPath, ($record | ConvertTo-Json -Depth 40), (New-Object Text.UTF8Encoding($false)))
        Write-Output "Demo result: $resultPath"
        Write-Output "Compose log: $logPath"

        if (-not $KeepRunning) {
            Invoke-Compose @("down")
            Write-Output "Docker demo service stopped after the run. Use -KeepRunning to leave it running."
        }
        break
    }
}
