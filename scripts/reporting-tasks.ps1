param(
    [ValidateSet('Install', 'Status', 'Stop', 'Uninstall')]
    [string]$Action = 'Status',
    [string]$HostRoot = (Split-Path $PSScriptRoot -Parent)
)
$ErrorActionPreference = 'Stop'
$HostRoot = (Resolve-Path -LiteralPath $HostRoot).Path
$pythonPath = Join-Path $HostRoot '.venv-windows\Scripts\pythonw.exe'
$taskNames = @('NQC-Portfolio-Reporting', 'NQC-News-Reporting')

if ($Action -eq 'Install') {
    if (!(Test-Path -LiteralPath $pythonPath)) { throw "Windows venv missing at $pythonPath" }
    if (!(Test-Path -LiteralPath (Join-Path $HostRoot 'dashboard\reporting_service.py'))) {
        throw 'Install the reporting code in the host checkout first.'
    }
    $taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
    $triggers = @(
        (New-ScheduledTaskTrigger -AtLogOn -User $taskUser),
        (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes 5))
    )
    foreach ($service in @('portfolio', 'news')) {
        $taskName = if ($service -eq 'portfolio') { $taskNames[0] } else { $taskNames[1] }
        $taskAction = New-ScheduledTaskAction -Execute $pythonPath `
            -Argument "-u -m dashboard.reporting_service $service" -WorkingDirectory $HostRoot
        # Updating these exact tasks is idempotent; no strategy tasks are registered.
        Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $triggers `
            -Settings $settings -Principal $principal `
            -Description 'Nova read-only reporting; requires Windows login, awake host and authenticated TWS for portfolio.' `
            -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
    }
} elseif ($Action -eq 'Stop' -or $Action -eq 'Uninstall') {
    foreach ($taskName in $taskNames) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) {
            Disable-ScheduledTask -TaskName $taskName | Out-Null
            Stop-ScheduledTask -TaskName $taskName
            if ($Action -eq 'Uninstall') { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
        }
    }
}

foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        [pscustomobject]@{ Task = $taskName; State = $task.State; LastRun = $info.LastRunTime;
            LastResult = $info.LastTaskResult; NextRun = $info.NextRunTime }
    }
}
