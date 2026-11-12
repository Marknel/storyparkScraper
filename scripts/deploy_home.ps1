#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$SourcePath = (Split-Path -Parent $PSScriptRoot),
    [string]$TargetPath = 'C:\VMs\storyparkScraper',
    [string]$SourceRevision = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-FullPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Get-SafePath([string]$Root, [string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "Unsafe relative deployment path: $Relative"
    }
    $result = Get-FullPath (Join-Path $Root $Relative)
    if (-not $result.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Deployment path escapes its root: $Relative"
    }
    $ancestor = Split-Path -Parent $result
    while ($ancestor.Length -ge $Root.Length) {
        if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Deployment refuses a reparse-point directory: $ancestor"
        }
        if ($ancestor -eq $Root) { break }
        $ancestor = Split-Path -Parent $ancestor
    }
    if ((Test-Path -LiteralPath $result) -and ((Get-Item -LiteralPath $result -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Deployment refuses a reparse-point destination: $result"
    }
    return $result
}

function Get-Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-Git([string]$Root, [string[]]$Arguments) {
    $result = & git -C $Root @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Git inspection failed in $Root." }
    return $result
}

function Test-ManagedPath([string]$Relative) {
    return $Relative -match '^(storypark_scraper/|scripts/|tests/|docs/|deployment/|\.github/)' -or
        $Relative -in @('main.py', 'requirements.txt', 'readme.md', 'Dockerfile', 'docker-compose.yaml', 'entrypoint.sh', 'LICENSE', '.gitignore', 'TODO.md')
}

function Write-JsonFile([string]$Path, $Value) {
    $temporary = $Path + '.new'
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Protect-BackupDirectory([string]$Path) {
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true, $false)
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    foreach ($sid in @($currentSid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'), [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

if (-not $IsWindows) { throw 'This deployment script targets the Windows main-pc host.' }
$sourceRoot = Get-FullPath (Resolve-Path -LiteralPath $SourcePath).ProviderPath
$targetRoot = Get-FullPath (Resolve-Path -LiteralPath $TargetPath).ProviderPath
if ($sourceRoot -eq $targetRoot -or $sourceRoot.StartsWith($targetRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Use a separate staging checkout or runner workspace, outside the live deployment.'
}
Get-Command git -ErrorAction Stop | Out-Null
$python = Join-Path $targetRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Existing target Python not found: $python" }
$revision = [string](Invoke-Git $sourceRoot @('rev-parse', 'HEAD'))
if ($SourceRevision -and $revision.Trim() -ne $SourceRevision) { throw 'Checkout does not match the requested source revision.' }
$dirtySource = @(Invoke-Git $sourceRoot @('diff', '--name-only', 'HEAD'))
if ($dirtySource.Count) { throw 'The staging checkout has tracked changes. Deploy a reviewed commit.' }
$files = @(Invoke-Git $sourceRoot @('ls-files') | Where-Object { Test-ManagedPath $_ })
if (-not $files.Count) { throw 'No tracked application source files were selected.' }
foreach ($relative in $files) {
    $source = Get-SafePath $sourceRoot $relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Tracked source is missing: $relative" }
    if ((Get-Item -LiteralPath $source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Source is a reparse point: $relative" }
    Get-SafePath $targetRoot $relative | Out-Null
}

# The child uses the deployment's configured run lock and keeps it until stdin closes.
# Playwright login and normal archive runs use the same lock. No user process is killed.
$lockCode = @'
import sys
from pathlib import Path
from storypark_scraper.config import load_config
from storypark_scraper.locking import acquire_run_lock
config = load_config(Path(sys.argv[1]))
with acquire_run_lock(config.paths.lock_file):
    print("LOCKED", flush=True)
    sys.stdin.read(1)
'@
$start = [Diagnostics.ProcessStartInfo]::new($python)
$start.WorkingDirectory = $targetRoot
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
foreach ($argument in @('-u', '-c', $lockCode, (Join-Path $targetRoot 'config.yaml'))) { $start.ArgumentList.Add($argument) }
$lockProcess = [Diagnostics.Process]::new()
$lockProcess.StartInfo = $start
$lockStarted = $false
$backupRoot = $null
$before = @{}
$changed = [Collections.Generic.List[string]]::new()
$temporaryFiles = [Collections.Generic.List[string]]::new()
$manifestPath = Join-Path $targetRoot '.deployment\current.json'
$previousManifest = $null
$requirementsChanged = $false
try {
    $lockStarted = $lockProcess.Start()
    if (-not $lockStarted) { throw 'Could not start the deployment lock holder.' }
    $handshake = $lockProcess.StandardOutput.ReadLineAsync()
    if (-not $handshake.Wait(10000) -or $handshake.Result -ne 'LOCKED') {
        throw 'Could not acquire the configured run lock. Finish the current run/login, then retry.'
    }

    $expectedDirtyHashes = @{
        'storypark_scraper/app.py' = '452bada4c4c2abc207c34b9d34a3b4b0b332a3715abb72f7c42cd67a9cbcc7bb'
        'storypark_scraper/cli.py' = '39f0b53540d21454036e28524f3a78c9989145f377e0a3523543c713c5445c8a'
        'storypark_scraper/config.py' = '82efa6420a1bc35e7e1a1bb3537021c18b74a0cf4579315fac8515841a392f0e'
        'tests/test_app.py' = 'e0df1ae32756837b0037d0e839b27000dc037ade0f3eca0ad2018232198f2fe5'
    }
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        $previousManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -AsHashtable
        foreach ($entry in $previousManifest.files.GetEnumerator()) {
            if (-not (Test-ManagedPath $entry.Key) -or $entry.Key -notin $files) { throw 'A managed source deletion requires manual review before deployment.' }
            $existing = Get-SafePath $targetRoot $entry.Key
            if (-not (Test-Path -LiteralPath $existing -PathType Leaf) -or (Get-Hash $existing) -ne $entry.Value) {
                throw "Live source changed since the last deployment: $($entry.Key)"
            }
        }
    }
    else {
        $targetHead = [string](Invoke-Git $targetRoot @('rev-parse', 'HEAD'))
        if ($targetHead.Trim() -ne 'ef9490d671abdd05553adf434aae0452502a7e6b') { throw 'The first deployment baseline changed; inspect and refresh the migration guard.' }
        foreach ($relative in @(Invoke-Git $targetRoot @('diff', '--name-only', 'HEAD'))) {
            if ($relative -eq 'config.yaml') { continue }
            if (-not $expectedDirtyHashes.ContainsKey($relative) -or (Get-Hash (Get-SafePath $targetRoot $relative)) -ne $expectedDirtyHashes[$relative]) {
                throw "Unreviewed deployment source changes: $relative"
            }
        }
    }
    $trackedTarget = @(Invoke-Git $targetRoot @('ls-files') | Where-Object { Test-ManagedPath $_ })
    if (@($trackedTarget | Where-Object { $_ -notin $files }).Count) { throw 'Source removes a deployed tracked file; review this migration manually.' }
    foreach ($relative in $files) {
        $destination = Get-SafePath $targetRoot $relative
        $exists = Test-Path -LiteralPath $destination -PathType Leaf
        if ($exists -and $relative -notin $trackedTarget -and ($null -eq $previousManifest -or -not $previousManifest.files.ContainsKey($relative))) {
            throw "An unmanaged deployment file would be overwritten: $relative"
        }
        $before[$relative] = if ($exists) { Get-Hash $destination } else { $null }
    }
    if ($previousManifest -and $previousManifest.revision -eq $revision.Trim()) {
        Write-Host "Revision $($revision.Trim()) is already deployed and its managed source hashes match."
        return
    }
    $requirementsChanged = $before['requirements.txt'] -ne (Get-Hash (Join-Path $sourceRoot 'requirements.txt'))
    $backupRoot = Join-Path $targetRoot ('.deployment\backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Protect-BackupDirectory $backupRoot
    foreach ($relative in $files) {
        if ($null -eq $before[$relative]) { continue }
        $backup = Get-SafePath $backupRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
        Copy-Item -LiteralPath (Get-SafePath $targetRoot $relative) -Destination $backup
        if ((Get-Hash $backup) -ne $before[$relative]) { throw "Backup verification failed: $relative" }
    }
    Write-JsonFile (Join-Path $backupRoot 'manifest.json') @{ revision = $revision.Trim(); target = $targetRoot; before = $before; created_at = [DateTime]::UtcNow.ToString('o') }
    foreach ($relative in $files) {
        $destination = Get-SafePath $targetRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        $temporary = $destination + '.deploying-' + [guid]::NewGuid().ToString('N')
        $temporaryFiles.Add($temporary)
        Copy-Item -LiteralPath (Get-SafePath $sourceRoot $relative) -Destination $temporary -Force
        if ((Get-Hash $temporary) -ne (Get-Hash (Get-SafePath $sourceRoot $relative))) { throw "Staged source hash mismatch: $relative" }
        $changed.Add($relative)
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }
    & $python -c 'import playwright, requests, yaml, bs4, paho.mqtt.client' 2>$null
    if ($requirementsChanged -or $LASTEXITCODE -ne 0) {
        & $python -m pip install -r (Join-Path $targetRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    Push-Location $targetRoot
    try {
        & $python -m storypark_scraper --help
        if ($LASTEXITCODE -ne 0) { throw 'Deployed CLI import/argument check failed.' }
    }
    finally { Pop-Location }
    $deployedHashes = @{}
    foreach ($relative in $files) {
        $destination = Get-SafePath $targetRoot $relative
        $deployedHashes[$relative] = Get-Hash $destination
        if ($deployedHashes[$relative] -ne (Get-Hash (Get-SafePath $sourceRoot $relative))) { throw "Final source verification failed: $relative" }
    }
    Write-JsonFile $manifestPath @{ revision = $revision.Trim(); deployed_at = [DateTime]::UtcNow.ToString('o'); backup = $backupRoot; files = $deployedHashes }
    Write-Host "Deployed $($revision.Trim()). Source backup: $backupRoot"
    Write-Host 'Runtime configuration and pause state were preserved. No login or archive run was started.'
}
catch {
    $deploymentError = $_
    if ($backupRoot -and $changed.Count) {
        foreach ($relative in $changed) {
            $destination = Get-SafePath $targetRoot $relative
            if ($null -eq $before[$relative]) {
                if (Test-Path -LiteralPath $destination -PathType Leaf) { Remove-Item -LiteralPath $destination }
            }
            else {
                Copy-Item -LiteralPath (Get-SafePath $backupRoot $relative) -Destination $destination -Force
                if ((Get-Hash $destination) -ne $before[$relative]) { throw "Rollback verification failed for $relative. Backup: $backupRoot" }
            }
        }
        Write-Warning "Source rollback completed. Inspect dependency changes separately if installation ran. Backup: $backupRoot"
    }
    throw $deploymentError
}
finally {
    foreach ($temporary in $temporaryFiles) {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary }
    }
    if ($lockStarted) {
        $lockProcess.StandardInput.Close()
        if (-not $lockProcess.WaitForExit(5000)) { $lockProcess.Kill() }
    }
    $lockProcess.Dispose()
}
