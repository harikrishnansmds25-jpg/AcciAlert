param(
    [Parameter(Mandatory = $true)]
    [string]$TunnelUrl
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

function Invoke-Git {
    param([string[]]$GitArgs)
    $output = & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE."
    }
    return $output
}

try {
    $parsedUrl = $null
    $validUri = [Uri]::TryCreate($TunnelUrl, [UriKind]::Absolute, [ref]$parsedUrl)
    if (-not $validUri) {
        throw "Supply an HTTPS Cloudflare quick-tunnel URL such as https://name.trycloudflare.com."
    }
    if ($parsedUrl.Scheme -ne "https" -or $parsedUrl.Host -notmatch '^[a-z0-9-]+\.trycloudflare\.com$' -or $parsedUrl.Port -ne 443 -or $parsedUrl.AbsolutePath -ne "/" -or $parsedUrl.Query -or $parsedUrl.UserInfo) {
        throw "Supply an HTTPS Cloudflare quick-tunnel URL such as https://name.trycloudflare.com."
    }
    $TunnelUrl = $parsedUrl.GetLeftPart([UriPartial]::Authority).TrimEnd("/")
    $geoServerBase = "$TunnelUrl/geoserver"

    $branch = (Invoke-Git @("branch", "--show-current") | Out-String).Trim()
    if ($branch -ne "main") { throw "The current branch is '$branch'; switch to main before deployment." }

    $before = @(Invoke-Git @("status", "--porcelain", "--untracked-files=all"))
    if ($before.Count -gt 0) {
        throw "The working tree has changes. Commit or remove them before running deploy.bat; nothing was changed."
    }

    $webConfig = Join-Path $projectRoot "web/js/config.js"
    $docsConfig = Join-Path $projectRoot "docs/js/config.js"
    $backupConfig = "$webConfig.bak"
    if (-not (Test-Path -LiteralPath $webConfig -PathType Leaf)) { throw "Missing $webConfig" }
    if (-not (Test-Path -LiteralPath $docsConfig -PathType Leaf)) { throw "Missing $docsConfig" }
    if (Test-Path -LiteralPath $backupConfig) { throw "Backup already exists at $backupConfig; inspect it before continuing." }

    $source = [IO.File]::ReadAllText($webConfig)
    $match = [regex]::Match($source, '(?m)^window\.GEOSERVER_BASE\s*=\s*"([^"]*)";\s*$')
    if (-not $match.Success -or [regex]::Matches($source, '(?m)^window\.GEOSERVER_BASE\s*=').Count -ne 1) {
        throw "Could not identify one GEOSERVER_BASE assignment in web/js/config.js."
    }
    $currentUrl = $match.Groups[1].Value
    $docsSource = [IO.File]::ReadAllText($docsConfig)
    $docsMatch = [regex]::Match($docsSource, '(?m)^window\.GEOSERVER_BASE\s*=\s*"([^"]*)";\s*$')
    if (-not $docsMatch.Success -or [regex]::Matches($docsSource, '(?m)^window\.GEOSERVER_BASE\s*=').Count -ne 1) {
        throw "Could not identify one GEOSERVER_BASE assignment in docs/js/config.js."
    }
    foreach ($existingUrl in @($currentUrl, $docsMatch.Groups[1].Value)) {
        if ($existingUrl -and $existingUrl -notmatch '^http://localhost:8080/geoserver/?$' -and $existingUrl -ne $geoServerBase) {
            throw "A config file already contains a different URL. Review both configs before changing the tunnel URL."
        }
    }

    Copy-Item -LiteralPath $webConfig -Destination $backupConfig
    $updated = [regex]::Replace(
        $source,
        '(?m)^window\.GEOSERVER_BASE\s*=\s*"[^"]*";\s*$',
        ('window.GEOSERVER_BASE = "' + $geoServerBase + '";'),
        1
    )
    [IO.File]::WriteAllText($webConfig, $updated, [Text.UTF8Encoding]::new($false))
    Copy-Item -LiteralPath $webConfig -Destination $docsConfig -Force

    $after = @(Invoke-Git @("status", "--porcelain", "--untracked-files=all"))
    $changedPaths = @($after | ForEach-Object { $_.Substring(3).Replace('\', '/') } | Sort-Object -Unique)
    $expectedPaths = @("docs/js/config.js", "web/js/config.js")
    if (Compare-Object $expectedPaths $changedPaths) {
        throw "Unexpected working-tree changes detected after config update; nothing was staged."
    }

    Invoke-Git @("add", "--", "web/js/config.js", "docs/js/config.js") | Out-Null
    $staged = @(Invoke-Git @("diff", "--cached", "--name-only")) | ForEach-Object { $_.Replace('\', '/') }
    if (Compare-Object $expectedPaths $staged) { throw "The staged file list is unexpected; nothing was committed." }
    if ($staged | Where-Object { $_ -match '(^|/)(\.env|.*\.bak|credentials|secrets|.*\.pem|.*\.key)$|(^|/)geoserver_data/' }) {
        throw "A sensitive, backup, or GeoServer data file is staged; nothing was committed."
    }
    Invoke-Git @("diff", "--cached", "--check") | Out-Null
    $stagedText = (Invoke-Git @("diff", "--cached", "--", "web/js/config.js", "docs/js/config.js") | Out-String)
    if ($stagedText -match '(?i)(github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|(?:password|passwd|client_secret|api[_-]?key|access[_-]?token)\s*[:=]\s*["''][^"'']{6,})') {
        throw "A possible credential was found in the staged config diff; nothing was committed."
    }

    Invoke-Git @("commit", "-m", "Update optional GeoServer tunnel URL") | Out-Null
    Invoke-Git @("push", "origin", "main") | Out-Null
    Write-Host "Pushed the tunnel configuration to main."
    Write-Host "Reminder: update GeoServer's Proxy Base URL to $geoServerBase."
}
catch {
    Write-Error $_
    exit 1
}
