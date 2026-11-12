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
    $accounts = @(Get-ScheduledTask | Where-Object {
        @($_.Actions | Where-Object { $_.Execute -eq $python -or $_.WorkingDirectory -eq $liveRoot }).Count -gt 0
    } | ForEach-Object { $_.Principal.UserId } | Select-Object -Unique)
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

$securePat = Read-Host "GitHub PAT with Administration write for private $DeploymentRepository (not the ADO PAT)" -AsSecureString
$authHeaders = @{}
$registration = $null
try {
    $plainPat = [Net.NetworkCredential]::new('', $securePat).Password
    $authHeaders = $publicHeaders.Clone()
    $authHeaders.Authorization = "Bearer $plainPat"
    $controlRepository = Invoke-RestMethod -Uri "https://api.github.com/repos/$DeploymentRepository" -Headers $authHeaders
    if (-not $controlRepository.private -or $controlRepository.full_name -ine $DeploymentRepository) {
        throw 'The home runner must be registered only to the named private deployment repository.'
    }
    $registration = Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$DeploymentRepository/actions/runners/registration-token" -Headers $authHeaders
}
finally {
    $plainPat = $null
    $authHeaders.Clear()
    $securePat.Dispose()
}
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
