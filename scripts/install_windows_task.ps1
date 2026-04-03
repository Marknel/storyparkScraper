param(
    [string]$TaskName = "StoryparkScraper",
    [string]$RunAt = "03:00",
    [string]$PythonExe = "",
    [string]$ConfigPath = "",
    [string]$User = ""
)

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonExe) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $ProjectRoot "config.yaml"
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m storypark_scraper --config `"$ConfigPath`" run" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($RunAt, 'HH:mm', $null))
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

if ($User) {
    $Credential = Get-Credential -UserName $User -Message "Enter the password for the scheduled task account"
    $Bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential.Password)
    try {
        $PlainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr)
    }
    finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
    }

    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -User $Credential.UserName -Password $PlainPassword -Force | Out-Null
}
else {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
}

Write-Host "Scheduled task '$TaskName' registered."
