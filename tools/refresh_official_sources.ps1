[CmdletBinding()]
param(
    [string]$OutputRoot = (Join-Path $PSScriptRoot '..\demo-data\official-sources'),
    [switch]$SkipManifestUpdate
)

$ErrorActionPreference = 'Stop'
$rawRoot = Join-Path $OutputRoot 'raw'
New-Item -ItemType Directory -Force -Path $rawRoot | Out-Null

$downloads = @(
    @{
        Url = 'https://raw.githubusercontent.com/fastapi/fastapi/master/docs/en/docs/tutorial/security/index.md'
        Path = (Join-Path $rawRoot 'fastapi-security.md')
    },
    @{
        Url = 'https://raw.githubusercontent.com/fastapi/fastapi/master/docs/en/docs/deployment/index.md'
        Path = (Join-Path $rawRoot 'fastapi-deployment.md')
    },
    @{
        Url = 'https://raw.githubusercontent.com/fastapi/fastapi/master/LICENSE'
        Path = (Join-Path $rawRoot 'fastapi-license.txt')
    },
    @{
        Url = 'https://raw.githubusercontent.com/OAI/OpenAPI-Specification/main/versions/3.2.1.md'
        Path = (Join-Path $rawRoot 'openapi-3.2.1.md')
    },
    @{
        Url = 'https://raw.githubusercontent.com/OAI/OpenAPI-Specification/main/LICENSE'
        Path = (Join-Path $rawRoot 'openapi-license.txt')
    },
    @{
        Url = 'https://raw.githubusercontent.com/compose-spec/compose-spec/main/spec.md'
        Path = (Join-Path $rawRoot 'compose-spec.md')
    },
    @{
        Url = 'https://raw.githubusercontent.com/compose-spec/compose-spec/main/LICENSE'
        Path = (Join-Path $rawRoot 'compose-spec-license.txt')
    },
    @{
        Url = 'https://raw.githubusercontent.com/python/cpython/main/Doc/library/venv.rst'
        Path = (Join-Path $rawRoot 'python-venv.rst')
    },
    @{
        Url = 'https://raw.githubusercontent.com/python/cpython/main/LICENSE'
        Path = (Join-Path $rawRoot 'python-license.txt')
    },
    @{
        Url = 'https://raw.githubusercontent.com/langgenius/dify/main/LICENSE'
        Path = (Join-Path $rawRoot 'dify-license.txt')
    }
)

foreach ($item in $downloads) {
    Invoke-WebRequest -Uri $item.Url -OutFile $item.Path
    $hash = (Get-FileHash $item.Path -Algorithm SHA256).Hash
    Write-Output ("{0}  {1}" -f $hash, $item.Path)
}

if (-not $SkipManifestUpdate) {
    $manifestPath = Join-Path $OutputRoot 'sources.jsonl'
    $manifestLines = Get-Content -LiteralPath $manifestPath
    $updatedLines = foreach ($line in $manifestLines) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        $entry = $line | ConvertFrom-Json
        if ($entry.local_path -and $entry.content_kind -in @('raw_markdown', 'raw_rst', 'license_text')) {
            $localPath = Join-Path $OutputRoot $entry.local_path
            if (Test-Path -LiteralPath $localPath) {
                $entry.sha256 = (Get-FileHash -LiteralPath $localPath -Algorithm SHA256).Hash
                $entry.retrieved_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
            }
        }
        $entry | ConvertTo-Json -Compress
    }
    Set-Content -LiteralPath $manifestPath -Value $updatedLines -Encoding utf8
    Write-Output ("Updated manifest: {0}" -f $manifestPath)
}

Write-Output 'Raw files refreshed. Recompute sources.jsonl hashes before committing and review all summaries against the current official pages.'
Write-Output 'FastGPT is represented by an attributed summary; no FastGPT page is copied verbatim.'
