param(
    [string]$TaskName = "StoryparkScraper",
    [string]$RunAt = "03:00",
    [string]$PythonExe = "",
    [string]$ConfigPath = "",
    [string]$User = "",
    [ValidateSet("Interactive", "S4U", "Password")]
    [string]$LogonType = "Interactive"
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
    if ($LogonType -eq "Password") {
        $Credential = Get-Credential -UserName $User -Message "Enter the password for the scheduled task account"
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -User $Credential.UserName -Password ($Credential.GetNetworkCredential().Password) -Force | Out-Null
    }
    else {
        $Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType $LogonType
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
    }
}
else {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
}

Write-Host "Scheduled task '$TaskName' registered successfully."
Write-Host "Note: Store Storypark credentials securely in Windows Credential Manager using:"
Write-Host "  python -m storypark_scraper store-credentials --username <your-email>"
