#requires -Version 7.2
#requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$InstallPath = 'C:\actions-runner-storypark',
    [string]$TargetPath = 'C:\VMs\storyparkScraper',
    [string]$WindowsAccount = '',
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    [string]$DeploymentRepository = 'Marknel/storyparkScraper-deploy'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-CachedGitHubCredential([string]$Repository) {
    $overrides = @{
        GCM_INTERACTIVE = 'never'
        GIT_TERMINAL_PROMPT = '0'
        GCM_GUI_PROMPT = '0'
        GCM_TRACE = '0'
        GCM_TRACE_SECRETS = '0'
        GIT_TRACE = '0'
        GIT_TRACE_CURL = '0'
        GIT_CURL_VERBOSE = '0'
    }
    $previous = @{}
    try {
        foreach ($entry in $overrides.GetEnumerator()) {
            $previous[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, 'Process')
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
        }
        $request = @('protocol=https', 'host=github.com', "path=$Repository.git", '', '') -join [char]10
        $credentialLines = $request | & git credential fill 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        foreach ($line in $credentialLines) {
            if ($line -match '^password=(.+)$') { return $Matches[1] }
        }
        return $null
    }
    catch { return $null }
    finally {
        foreach ($entry in $previous.GetEnumerator()) {
            if ($null -eq $entry.Value) { Remove-Item -LiteralPath ("Env:" + $entry.Key) -ErrorAction SilentlyContinue }
            else { [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process') }
        }
    }
}

function Request-PrivateRunnerRegistration([string]$Repository, [string]$CredentialToken, [hashtable]$Headers) {
    $authHeaders = $Headers.Clone()
    try {
        $authHeaders.Authorization = "Bearer $CredentialToken"
        $controlRepository = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository" -Headers $authHeaders
        if (-not $controlRepository.private -or $controlRepository.full_name -ine $Repository) {
            throw 'The home runner must be registered only to the named private deployment repository.'
        }
        return Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$Repository/actions/runners/registration-token" -Headers $authHeaders
    }
    finally { $authHeaders.Clear() }
}

function Get-HomeRunnerRegistration([string]$Repository, [hashtable]$Headers) {
    $cachedToken = Get-CachedGitHubCredential $Repository
    if ($cachedToken) {
        try {
            return Request-PrivateRunnerRegistration $Repository $cachedToken $Headers
        }
        catch {
            $response = $_.Exception.PSObject.Properties['Response']
            $status = if ($response -and $response.Value) { [int]$response.Value.StatusCode } else { 0 }
            if ($status -notin @(401, 403, 404)) { throw }
        }
        finally { $cachedToken = $null }
    }
    $securePat = Read-Host "GitHub PAT with Administration write for private $Repository (not the ADO PAT)" -AsSecureString
    try {
        $plainPat = [Net.NetworkCredential]::new('', $securePat).Password
        return Request-PrivateRunnerRegistration $Repository $plainPat $Headers
    }
    finally {
        $plainPat = $null
        $securePat.Dispose()
    }
}

function Get-ScraperTaskAccounts([object[]]$Tasks, [string]$Python, [string]$TargetRoot) {
    $root = $TargetRoot.Replace('/', '\').TrimEnd('\')
    $rootArgument = '(?i)(?<![A-Za-z0-9_.-])' + [regex]::Escape($root) + '(?:\\|["''\s]|$)'
    foreach ($task in $Tasks) {
        $actionsProperty = $task.PSObject.Properties['Actions']
        if (-not $actionsProperty) { continue }
        $matchesTarget = $false
        foreach ($action in @($actionsProperty.Value)) {
            if ($null -eq $action) { continue }
            # COM handler and other non-Exec actions do not expose these properties.
            $executeProperty = $action.PSObject.Properties['Execute']
            if (-not $executeProperty -or -not $executeProperty.Value) { continue }
            $execute = [Environment]::ExpandEnvironmentVariables([string]$executeProperty.Value).Trim().Trim('"')
            $workingProperty = $action.PSObject.Properties['WorkingDirectory']
            $working = if ($workingProperty) { [Environment]::ExpandEnvironmentVariables([string]$workingProperty.Value).Trim().Trim('"').TrimEnd('\') } else { '' }
            $argumentsProperty = $action.PSObject.Properties['Arguments']
            $arguments = if ($argumentsProperty) { [Environment]::ExpandEnvironmentVariables([string]$argumentsProperty.Value).Replace('/', '\') } else { '' }
            if ($execute -ieq $Python -or $working -ieq $root -or $arguments -match $rootArgument) {
                $matchesTarget = $true
                break
            }
        }
        if ($matchesTarget) {
            $principalProperty = $task.PSObject.Properties['Principal']
            $userProperty = if ($principalProperty -and $principalProperty.Value) { $principalProperty.Value.PSObject.Properties['UserId'] } else { $null }
            if (-not $userProperty -or -not $userProperty.Value) { throw 'A matching scraper task has no readable Windows account. Specify -WindowsAccount explicitly.' }
            [string]$userProperty.Value
        }
    }
}

if (-not $IsWindows -or $env:COMPUTERNAME -ine 'main-pc') { throw 'Run this installer in the main-pc RDP session.' }
foreach ($command in @('git', 'pwsh')) { Get-Command $command -ErrorAction Stop | Out-Null }
$python = Join-Path $TargetPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Existing deployment Python not found: $python" }
$runnerRoot = [IO.Path]::GetFullPath($InstallPath).TrimEnd('\')
$liveRoot = [IO.Path]::GetFullPath($TargetPath).TrimEnd('\')
if ($runnerRoot -eq $liveRoot -or $runnerRoot.StartsWith($liveRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Install the runner outside the live Storypark deployment.'
}
if ((Test-Path -LiteralPath $runnerRoot) -and @(Get-ChildItem -LiteralPath $runnerRoot -Force).Count) {
    throw 'Runner directory is not empty. Inspect the existing installation instead of replacing it.'
}
$ancestor = $runnerRoot
while ($ancestor) {
    if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Runner installation refuses a reparse-point directory: $ancestor"
    }
    $ancestor = Split-Path -Parent $ancestor
}
if (-not $WindowsAccount) {
    $accounts = @(Get-ScraperTaskAccounts -Tasks @(Get-ScheduledTask) -Python $python -TargetRoot $liveRoot | Select-Object -Unique)
    if ($accounts.Count -gt 1) { throw 'Multiple scraper task identities found. Specify -WindowsAccount explicitly.' }
    $WindowsAccount = if ($accounts.Count -eq 1) { $accounts[0] } else { [Security.Principal.WindowsIdentity]::GetCurrent().Name }
}
if ($WindowsAccount -in @('SYSTEM', 'NT AUTHORITY\SYSTEM', 'S-1-5-18')) { throw 'Choose the normal Windows scraper account rather than SYSTEM.' }
Write-Host "Runner service account: $WindowsAccount"
Write-Host 'Use the same account as the existing scraper/profile. Native runner setup will prompt for its service password.'

$publicHeaders = @{ Accept = 'application/vnd.github+json'; 'X-GitHub-Api-Version' = '2022-11-28'; 'User-Agent' = 'storypark-home-runner-setup' }
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/actions/runner/releases/latest' -Headers $publicHeaders
$version = $release.tag_name.TrimStart('v')
$assetName = "actions-runner-win-x64-$version.zip"
$asset = @($release.assets | Where-Object { $_.name -eq $assetName })
if ($asset.Count -ne 1) { throw 'The official Windows x64 runner asset was not found.' }
$digestMatch = [regex]::Match([string]$asset[0].digest, '^sha256:([a-fA-F0-9]{64})$')
if (-not $digestMatch.Success) {
    throw 'The official runner asset lacks a SHA256 digest. Do not install an unverified download.'
}
$expectedHash = $digestMatch.Groups[1].Value.ToLowerInvariant()
$downloadUrl = [uri]$asset[0].browser_download_url
if ($downloadUrl.Scheme -ne 'https' -or $downloadUrl.Host -ne 'github.com' -or $downloadUrl.AbsolutePath -notlike '/actions/runner/releases/download/*') {
    throw 'Unexpected runner download origin.'
}
$zip = Join-Path ([IO.Path]::GetTempPath()) ("storypark-runner-" + [guid]::NewGuid().ToString('N') + '.zip')
try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $zip
    if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Runner SHA256 verification failed.' }
    New-Item -ItemType Directory -Path $runnerRoot -Force | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $runnerRoot
}
finally { if (Test-Path -LiteralPath $zip -PathType Leaf) { Remove-Item -LiteralPath $zip } }

$registration = Get-HomeRunnerRegistration $DeploymentRepository $publicHeaders
if (-not $registration.token) { throw 'GitHub did not return a runner registration token.' }
Push-Location $runnerRoot
try {
    # Only the short-lived, one-time registration token is a process argument.
    # A persistent PAT and Windows password are never passed as arguments or written to a file.
    & .\config.cmd --url "https://github.com/$DeploymentRepository" --token $registration.token --name 'main-pc-storypark' --labels 'main-pc,storypark-deploy' --work '_work' --runasservice --windowslogonaccount $WindowsAccount
    if ($LASTEXITCODE -ne 0) { throw 'Runner registration/service configuration did not complete.' }
}
finally {
    $registration = $null
    Pop-Location
}
Write-Host "Runner configured. Verify main-pc-storypark is Online in private $DeploymentRepository before deploying."
Write-Host 'The private deploy workflow is manual by default; enable HOME_AUTO_DEPLOY=true there only after a successful reviewed deployment.'
