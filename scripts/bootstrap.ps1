param(
    [string]$Python = "python",
    [string]$VenvPath = ".venv",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FullVenvPath = Join-Path $ProjectRoot $VenvPath
$PythonExe = Join-Path $FullVenvPath "Scripts\python.exe"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"
$SitePackages = Join-Path $FullVenvPath "Lib\site-packages"
$BootstrapTemp = Join-Path $ProjectRoot ".bootstrap-tmp"

New-Item -ItemType Directory -Force $BootstrapTemp | Out-Null
$env:TMP = $BootstrapTemp
$env:TEMP = $BootstrapTemp

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $($LASTEXITCODE): $FilePath $($ArgumentList -join ' ')"
    }
}

function Initialize-Venv {
    param([switch]$ForceClear)

    $Args = @("-m", "venv", "--without-pip")
    if ($ForceClear) {
        $Args += "--clear"
    }
    $Args += $FullVenvPath
    Invoke-External $Python @Args
}

function Test-CorruptPipArtifacts {
    if (-not (Test-Path $SitePackages)) {
        return $false
    }

    $Artifacts = Get-ChildItem -Force $SitePackages | Where-Object { $_.Name -like '~*' }
    return $Artifacts.Count -gt 0
}

if ($Recreate -or -not (Test-Path $PythonExe)) {
    Initialize-Venv -ForceClear
}
else {
    Initialize-Venv
}

if (Test-CorruptPipArtifacts) {
    Write-Warning "Detected stale pip artifacts in the virtual environment. Rebuilding .venv."
    Initialize-Venv -ForceClear
}

Invoke-External $PythonExe -m ensurepip --upgrade
Invoke-External $PythonExe -m pip install -r $RequirementsPath
Invoke-External $PythonExe -m playwright install chromium

Write-Host "Bootstrap complete."
Write-Host "Store credentials with: $PythonExe -m storypark_scraper --config `"$(Join-Path $ProjectRoot 'config.yaml')`" store-credentials --username you@example.com"
