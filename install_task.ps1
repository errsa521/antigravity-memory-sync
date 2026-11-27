param(
    [switch]$Uninstall,
    [int]$IntervalMinutes = 60
)

$TaskName = "AntigravityMemorySync"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SyncScript = Join-Path $ScriptDir "sync.py"
$LogFile = Join-Path $ScriptDir "state\sync.log"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName"
    } else {
        Write-Host "Scheduled task not found: $TaskName"
    }
    exit 0
}

$PythonExe = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = (Get-Command python.exe -ErrorAction Stop).Source
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile) | Out-Null

# Run pythonw.exe directly (no console flash); sync.py writes bounded state\sync.log itself
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$SyncScript`"" `
    -WorkingDirectory $ScriptDir

# Trigger 1: At user logon (catch-up immediately after boot)
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Trigger 2: Every N minutes continuously
$TriggerRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($TriggerLogon, $TriggerRepeat) `
    -Settings $Settings `
    -Description "Hana-style rolling memory sync from Antigravity transcripts to Basic Memory." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' (every $IntervalMinutes min + at logon, StartWhenAvailable=ON)."
Write-Host "Log file: $LogFile"
