# Windows Task Scheduler equivalent of the launchd/systemd agents.
# Run once in an elevated PowerShell from the repo root:
#     powershell -ExecutionPolicy Bypass -File deploy\windows-schedule.ps1
#
# -StartWhenAvailable is the Windows analogue of launchd wake-catch-up and
# systemd Persistent=true: a run missed while the machine was off fires when it
# next boots. Without it a missed session is a permanent hole in the
# point-in-time surveillance record, which NSE cannot serve retroactively.

$repo = (Get-Location).Path
$py   = (Get-Command python).Source

$daily = New-ScheduledTaskAction -Execute $py `
    -Argument "snapshot.py" -WorkingDirectory $repo
$dailyCatchup = New-ScheduledTaskAction -Execute $py `
    -Argument "snapshot.py --catchup" -WorkingDirectory $repo
$dailyRun = New-ScheduledTaskAction -Execute $py `
    -Argument "runner.py" -WorkingDirectory $repo

$trigger  = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 7:00PM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName "TradingBot-Daily" `
    -Action $daily,$dailyCatchup,$dailyRun -Trigger $trigger `
    -Settings $settings -Description "NSE snapshot + paper runner" -Force

$weeklyAction  = New-ScheduledTaskAction -Execute $py `
    -Argument "generator.py -n 400" -WorkingDirectory $repo
$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 9:00AM

Register-ScheduledTask -TaskName "TradingBot-Weekly" `
    -Action $weeklyAction -Trigger $weeklyTrigger -Settings $settings `
    -Description "Weekly research pass" -Force

Write-Host ""
Write-Host "Registered. Verify:  Get-ScheduledTask -TaskName TradingBot-*"
Write-Host ""
Write-Host "WARNING: do NOT run searches here if another machine is also"
Write-Host "searching. The holdout budget in data/judge_ledger_epoch2.json is"
Write-Host "shared; two independent ledgers silently double the hypotheses"
Write-Host "tested. Data collection on both machines is safe."
