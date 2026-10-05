param(
    [ValidateSet("Demo", "Build", "Up", "Down", "Config", "Logs")]
    [string]$Action = "Demo",
    [int]$ApiPort = 8001,
    [int]$TimeoutSeconds = 180,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$composeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.enterprise.yml")
$env:API_PORT = "$ApiPort"
$env:ENTERPRISE_VAULT_HOST_PATH = Join-Path $repoRoot "demo-data\enterprise-vault"
$env:ENTERPRISE_INDEX_HOST_PATH = Join-Path $repoRoot "demo-data\enterprise-index"
$env:ENTERPRISE_TEMP_HOST_PATH = Join-Path $repoRoot "demo-data\enterprise-temp"
$env:INDEX_HOST_PATH = $env:ENTERPRISE_INDEX_HOST_PATH
$env:OBSIDIAN_HOST_PATH = $env:ENTERPRISE_VAULT_HOST_PATH
$baseUri = "http://127.0.0.1:$ApiPort"
$reportDir = Join-Path $repoRoot "demo-data\enterprise-reports"
New-Item -ItemType Directory -Force -Path $env:ENTERPRISE_INDEX_HOST_PATH, $env:ENTERPRISE_TEMP_HOST_PATH, $reportDir | Out-Null

if (-not $env:BGE_MODEL_HOST_PATH) {
    $toolRoot = if ($env:TOOL_DIR) { $env:TOOL_DIR } else { "D:\tool" }
    $env:BGE_MODEL_HOST_PATH = Join-Path $toolRoot "bge-m3"
}
if (-not $env:API_TOKEN) {
    throw "API_TOKEN must be set before running the enterprise Docker demo."
}

function Invoke-EnterpriseCompose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker compose @composeFiles @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

switch ($Action) {
    "Config" {
        Invoke-EnterpriseCompose @("config", "--no-interpolate")
        break
    }
    "Up" {
        Invoke-EnterpriseCompose @("up", "-d", "--build")
        Write-Output "Enterprise Docker demo started at $baseUri"
        break
    }
    "Build" {
        Invoke-EnterpriseCompose @("run", "--rm", "--no-deps", "--build", "--entrypoint", ".venv/bin/python", "rag-api", "tools/rebuild_index.py")
        Write-Output "Enterprise index built in $env:ENTERPRISE_INDEX_HOST_PATH"
        break
    }
    "Down" {
        Invoke-EnterpriseCompose @("down")
        Write-Output "Enterprise Docker demo stopped."
        break
    }
    "Logs" {
        Invoke-EnterpriseCompose @("logs", "--tail", "200", "rag-api")
        break
    }
    "Demo" {
        Invoke-EnterpriseCompose @("run", "--rm", "--no-deps", "--build", "--entrypoint", ".venv/bin/python", "rag-api", "tools/rebuild_index.py")
        Invoke-EnterpriseCompose @("up", "-d", "--build")
        $headers = @{ Authorization = "Bearer $($env:API_TOKEN)" }
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        $health = $null
        while ((Get-Date) -lt $deadline) {
            try {
                $health = Invoke-RestMethod -Method Get -Uri "$baseUri/health" -Headers $headers -TimeoutSec 10
                if ($health.service -eq "ready" -and $health.status -eq "ok") { break }
            } catch { $health = $null }
            Start-Sleep -Seconds 3
        }
        if ($null -eq $health -or $health.service -ne "ready" -or $health.status -ne "ok") {
            throw "Enterprise API did not become ready within $TimeoutSeconds seconds."
        }

        $cases = @(
            @{ name = "product_functions"; question = "星云企业工单平台有哪些核心功能？"; requireAnswer = $true; requireCitation = $true },
            @{ name = "ticket_troubleshooting"; question = "客服无法接收新工单时，应该如何排查？"; requireAnswer = $true; requireCitation = $true },
            @{ name = "version_changes"; question = "v1.2 相比旧版本增加了哪些功能？"; requireAnswer = $true; requireCitation = $true },
            @{ name = "private_deployment"; question = "企业要求私有化部署，需要准备哪些资源？"; requireAnswer = $true; requireCitation = $true },
            @{ name = "unsupported_order_mutation"; question = "这个系统能不能自动修改客户数据库中的订单金额？"; requireAnswer = $true; requireCitation = $false; requireBoundary = $true }
        )
        $responses = @()
        foreach ($case in $cases) {
            $body = @{
                question = $case.question
                top_k = 5
                filters = @{ domain = "enterprise" }
                rewrite = $false
                semantic_grounding = $true
            } | ConvertTo-Json -Depth 10
            $response = Invoke-RestMethod -Method Post -Uri "$baseUri/query" -Headers $headers -ContentType "application/json" -Body $body
            $retrievedDomains = @($response.retrieval.results | ForEach-Object { $_.domain } | Sort-Object -Unique)
            $answerNonempty = -not [string]::IsNullOrWhiteSpace([string]$response.answer)
            $citationCount = @($response.citations).Count
            $domainMatched = $retrievedDomains.Count -gt 0 -and @($retrievedDomains | Where-Object { $_ -ne "enterprise" }).Count -eq 0
            $citationMatched = (-not $case.requireCitation) -or $citationCount -gt 0
            $boundaryMatched = $true
            if ($case.requireBoundary) {
                $answerText = [string]$response.answer
                $boundaryMatched = ($answerText -match "证据不足|不支持|无法|不能|产品边界|人工") -and ($answerText -notmatch "支持.*自动修改|可以.*自动修改|能够.*自动修改")
            }
            $passed = $domainMatched -and $answerNonempty -and $citationMatched -and $boundaryMatched
            $responses += [ordered]@{ name = $case.name; question = $case.question; retrieved_domains = $retrievedDomains; answer_nonempty = $answerNonempty; citation_count = $citationCount; boundary_matched = $boundaryMatched; output_gate = $response.output_gate; confidence = $response.answer_confidence; answer_provider = $response.answer_provider; answer_model = $response.answer_model; answer_call_completed = $response.answer_call_completed; semantic_judge_used = $response.evidence_conflicts.used_llm; llm_usage = $response.llm_usage; passed = $passed }
        }
        $record = [ordered]@{ generated_at = (Get-Date).ToString("o"); status = if (@($responses | Where-Object { -not $_.passed }).Count -eq 0) { "passed" } else { "failed" }; health = $health; cases = $responses }
        $resultPath = Join-Path $reportDir "enterprise-demo-latest.json"
        $record | ConvertTo-Json -Depth 20 | Set-Content -Encoding utf8 $resultPath
        Write-Output "Enterprise demo report: $resultPath"
        if (-not $KeepRunning) { Invoke-EnterpriseCompose @("down") }
        if ($record.status -ne "passed") { throw "Enterprise Docker demo validation failed." }
        break
    }
}
