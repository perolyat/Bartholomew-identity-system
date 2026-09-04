<#
.SYNOPSIS
    Install, run, inspect and remove Bartholomew's Windows action companion.

.DESCRIPTION
    ONE script, several verbs, and it does NOTHING until you name one. Sourcing
    it, opening it, or running it with no arguments prints this help and exits;
    there is no code path that installs anything as a side effect of the file
    existing.

    Everything it touches is USER-LEVEL. It never writes to Program Files, never
    writes to HKLM, never creates a Windows service, never runs as SYSTEM, never
    changes a firewall rule, never touches Defender, never alters UAC, never
    changes an execution policy, and never installs a certificate. Every verb
    below states exactly what it changes before it changes it.

    What each verb changes:

      install     Creates %LOCALAPPDATA%\Bartholomew\ , an isolated Python
                  virtual environment inside it, installs Bartholomew and its
                  dependencies into that environment ONLY, and copies
                  companion.env.example to companion.env if none exists. It does
                  NOT start the companion and does NOT enable startup.

      start       Runs the companion in this window until you press Ctrl+C.
      start -Background
                  Runs it detached and records the pid so `stop` can find it.
      stop        Ends the companion this script started, and nothing else.
      status      Reports whether it is running, and what it is configured to do.
      diagnostics Prints the full configuration, the accessibility adapter's
                  availability, the ledger's state, and an enrolment record you
                  can paste on the Bartholomew side. Contacts nothing.

      enable-startup   Creates ONE user-level Scheduled Task, `BartholomewAction`,
                       under your own account, that starts the companion at your
                       logon. Prints exactly what it will create and asks first.
      disable-startup  Deletes that task. Nothing else.

      uninstall   Stops the companion, deletes the scheduled task if present,
                  and removes the virtual environment. KEEPS companion.env and
                  the ledger unless you pass -Purge, because those are yours.
      rollback    Restores the previous virtual environment from the backup
                  `install` takes, so a failed upgrade goes back to what worked.

.PARAMETER Verb
    One of: install, start, stop, status, diagnostics, enable-startup,
    disable-startup, uninstall, rollback.

.EXAMPLE
    .\bartholomew-action.ps1 install
    .\bartholomew-action.ps1 diagnostics
    .\bartholomew-action.ps1 start

.NOTES
    Requires Python 3.11+ on PATH. Run as your ordinary user; if this script is
    ever run elevated it says so and stops, because a companion running as
    Administrator would act on the computer with more authority than the person
    it is acting for.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        'install', 'start', 'stop', 'status', 'diagnostics',
        'enable-startup', 'disable-startup', 'uninstall', 'rollback'
    )]
    [string] $Verb,

    [switch] $Background,
    [switch] $Purge,
    [switch] $Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- where everything lives (user scope, all of it) ---------------------------

$Root          = Join-Path $env:LOCALAPPDATA 'Bartholomew'
$VenvPath      = Join-Path $Root 'venv'
$VenvBackup    = Join-Path $Root 'venv.previous'
$EnvFile       = Join-Path $Root 'companion.env'
$LogDir        = Join-Path $Root 'logs'
$LogFile       = Join-Path $LogDir 'action-companion.log'
$PidFile       = Join-Path $Root 'action-companion.pid'
$StateFile     = Join-Path $Root 'action-state.json'
$TaskName      = 'BartholomewAction'
$RepoRoot      = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ExampleEnv    = Join-Path $PSScriptRoot 'companion.env.example'
$PythonExe     = Join-Path $VenvPath 'Scripts\python.exe'

function Write-Section([string] $Text) {
    Write-Host ''
    Write-Host $Text -ForegroundColor Cyan
    Write-Host ('-' * $Text.Length) -ForegroundColor DarkCyan
}

function Confirm-Change([string] $Summary) {
    <#  Every changing verb states what it will do and asks, unless -Yes.
        A script that silently changed a machine would be the exact thing the
        capability model above it exists to prevent.  #>
    Write-Host ''
    Write-Host 'This will:' -ForegroundColor Yellow
    Write-Host $Summary
    if ($Yes) { Write-Host '(-Yes given; proceeding.)'; return $true }
    $answer = Read-Host 'Proceed? [y/N]'
    return $answer -match '^(y|yes)$'
}

function Assert-NotElevated {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw @'
This is running elevated, and it must not be.

The action companion acts on this computer on your behalf. Running it as
Administrator would give it more authority than you have in your ordinary
session, and nothing it does needs elevation: every path below is under
%LOCALAPPDATA% and every registry key it might touch belongs to your own user.

Close this window and run the same command from an ordinary PowerShell prompt.
'@
    }
}

function Get-SystemPython {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        $version = & $found.Source -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]'3.11') {
            return $found.Source
        }
    }
    throw 'Python 3.11 or newer was not found on PATH. Install it from python.org and try again.'
}

function Test-Installed { Test-Path $PythonExe }

function Get-RunningPid {
    if (-not (Test-Path $PidFile)) { return $null }
    $recorded = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $recorded) { return $null }
    $process = Get-Process -Id $recorded -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    # Only ever a process this script started: matched on the recorded pid AND
    # on it actually being a python process, so a recycled pid is not mistaken
    # for the companion and stopped.
    if ($process.ProcessName -notmatch 'python') { return $null }
    return [int] $recorded
}

function Import-CompanionEnv {
    if (-not (Test-Path $EnvFile)) {
        throw "No configuration at $EnvFile. Run: .\bartholomew-action.ps1 install"
    }
    $continuation = $null
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*#' -or $line -match '^\s*$') { continue }
        if ($line -match '^\s' -and $continuation) {
            # An indented line continues the previous value -- how the multi-line
            # application allowlist is written.
            $existing = [Environment]::GetEnvironmentVariable($continuation, 'Process')
            [Environment]::SetEnvironmentVariable(
                $continuation, "$existing`n$($line.Trim())", 'Process')
            continue
        }
        $split = $line.IndexOf('=')
        if ($split -lt 1) { continue }
        $name  = $line.Substring(0, $split).Trim()
        $value = $line.Substring($split + 1)
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        $continuation = $name
    }
}

# --- verbs ---------------------------------------------------------------------

function Invoke-Install {
    Assert-NotElevated
    $summary = @"
  * create $Root (if absent)
  * create an isolated Python virtual environment at $VenvPath
    (backing up any existing one to $VenvBackup first, so ``rollback`` works)
  * install Bartholomew and its dependencies INTO THAT ENVIRONMENT ONLY,
    from $RepoRoot -- your system Python is not modified
  * copy companion.env.example to $EnvFile if none exists, readable only by you

It will NOT start the companion, NOT enable startup, NOT create a service, NOT
write outside $Root, and NOT change any Windows security setting.
"@
    if (-not (Confirm-Change $summary)) { Write-Host 'Nothing was changed.'; return }

    $python = Get-SystemPython
    Write-Section 'Preparing'
    New-Item -ItemType Directory -Force -Path $Root, $LogDir | Out-Null

    if (Test-Path $VenvPath) {
        Write-Host "Backing up the existing environment to $VenvBackup"
        if (Test-Path $VenvBackup) { Remove-Item $VenvBackup -Recurse -Force }
        Move-Item $VenvPath $VenvBackup
    }

    Write-Section 'Creating the isolated environment'
    & $python -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }

    Write-Section 'Installing dependencies'
    & $PythonExe -m pip install --upgrade pip --quiet
    # The `windows` extra brings comtypes, which the accessibility adapter needs
    # for windows.type_text and windows.accessibility_action. Without it those
    # two capabilities REFUSE rather than degrade; every other one works.
    & $PythonExe -m pip install "$RepoRoot[windows]" --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Run `rollback` to restore the previous environment.' }

    if (-not (Test-Path $EnvFile)) {
        Write-Section 'Configuration'
        Copy-Item $ExampleEnv $EnvFile
        # Readable only by the installing user: the credential header lives here.
        $acl = Get-Acl $EnvFile
        $acl.SetAccessRuleProtection($true, $false)
        $acl.SetAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            [Security.Principal.WindowsIdentity]::GetCurrent().Name,
            'FullControl', 'Allow')))
        Set-Acl $EnvFile $acl
        Write-Host "Wrote $EnvFile (readable only by you)."
        Write-Host 'EDIT IT BEFORE STARTING. Nothing works until the three allowlists' -ForegroundColor Yellow
        Write-Host 'and the device credential are filled in -- and an empty allowlist' -ForegroundColor Yellow
        Write-Host 'permits nothing rather than everything.' -ForegroundColor Yellow
    } else {
        Write-Host "Keeping the existing $EnvFile (never overwritten)."
    }

    Write-Section 'Installed'
    Write-Host "Environment: $VenvPath"
    Write-Host "Config:      $EnvFile"
    Write-Host "Logs:        $LogFile"
    Write-Host ''
    Write-Host 'Next:  .\bartholomew-action.ps1 diagnostics    (check the configuration)'
    Write-Host '       .\bartholomew-action.ps1 start          (run it in this window)'
}

function Invoke-Start {
    Assert-NotElevated
    if (-not (Test-Installed)) { throw 'Not installed. Run: .\bartholomew-action.ps1 install' }
    $existing = Get-RunningPid
    if ($existing) { Write-Host "Already running (pid $existing)."; return }

    Import-CompanionEnv
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    if ($Background) {
        Write-Host "Starting in the background; logging to $LogFile"
        $process = Start-Process -FilePath $PythonExe `
            -ArgumentList '-m', 'bartholomew.windows_actuation', 'run' `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError "$LogFile.err" `
            -WindowStyle Hidden -PassThru
        Set-Content -Path $PidFile -Value $process.Id
        Write-Host "Started (pid $($process.Id)). Stop it with: .\bartholomew-action.ps1 stop"
    } else {
        Write-Host 'Running in this window. Press Ctrl+C to stop.'
        & $PythonExe -m bartholomew.windows_actuation run
    }
}

function Invoke-Stop {
    $running = Get-RunningPid
    if (-not $running) {
        Write-Host 'Not running (nothing this script started is alive).'
        if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
        return
    }
    Write-Host "Stopping pid $running"
    Stop-Process -Id $running -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    if (Get-Process -Id $running -ErrorAction SilentlyContinue) {
        Stop-Process -Id $running -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host 'Stopped.'
}

function Invoke-Status {
    Write-Section 'Bartholomew action companion'
    Write-Host ("Installed:      {0}" -f (Test-Installed))
    $running = Get-RunningPid
    Write-Host ("Running:        {0}" -f $(if ($running) { "yes (pid $running)" } else { 'no' }))
    Write-Host ("Config:         {0}" -f $(if (Test-Path $EnvFile) { $EnvFile } else { '(none)' }))
    Write-Host ("Ledger:         {0}" -f $(if (Test-Path $StateFile) { $StateFile } else { '(none yet)' }))
    Write-Host ("Log:            {0}" -f $(if (Test-Path $LogFile) { $LogFile } else { '(none yet)' }))
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host ("Starts at logon: {0}" -f $(if ($task) { 'yes' } else { 'no' }))
    # Parenthesised, and that is not style. `Test-Installed` has no `param()`
    # block, so PowerShell parses `Test-Installed -and (...)` as a *command
    # invocation* -- `-and` and the second test are swallowed into `$args` and
    # discarded, leaving the condition as `Test-Installed` alone. On a machine
    # where `install` ran but the configuration was never written, that entered
    # the branch, `Import-CompanionEnv` threw, and with
    # `$ErrorActionPreference = 'Stop'` the `status` verb died with a red error
    # immediately after printing "Config: (none)".
    if ((Test-Installed) -and (Test-Path $EnvFile)) {
        Write-Host ''
        Write-Host 'Configured capabilities:'
        Import-CompanionEnv
        & $PythonExe -c @'
import json, sys
from bartholomew.windows_actuation.config import load_config
try:
    caps = load_config().describe()["capabilities"]
except Exception as e:
    print(f"  configuration error: {e}"); sys.exit(0)
print("  " + (", ".join(caps) if caps else "(none -- this companion will refuse every action)"))
'@
    }
}

function Invoke-Diagnostics {
    if (-not (Test-Installed)) { throw 'Not installed. Run: .\bartholomew-action.ps1 install' }
    Import-CompanionEnv
    Write-Section 'Diagnostics (contacts nothing)'
    & $PythonExe -m bartholomew.windows_actuation diagnostics
}

function Invoke-EnableStartup {
    Assert-NotElevated
    if (-not (Test-Installed)) { throw 'Not installed. Run: .\bartholomew-action.ps1 install' }
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $summary = @"
  * create ONE Scheduled Task named '$TaskName'
  * running as: $user  (your own account, NOT SYSTEM, NOT elevated)
  * trigger:    your logon
  * command:    $PythonExe -m bartholomew.windows_actuation run
  * log:        $LogFile

It creates no service, no registry Run key, no HKLM entry, and nothing that
survives ``disable-startup``. Remove it any time with:
  .\bartholomew-action.ps1 disable-startup
"@
    if (-not (Confirm-Change $summary)) { Write-Host 'Nothing was changed.'; return }

    $action = New-ScheduledTaskAction -Execute $PythonExe `
        -Argument '-m bartholomew.windows_actuation run' -WorkingDirectory $Root
    $trigger  = New-ScheduledTaskTrigger -AtLogOn -User $user
    # -RunLevel Limited is explicit: never elevated, whatever the account can do.
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Limited -Force | Out-Null
    Write-Host "Created the scheduled task '$TaskName'. It starts at your next logon."
}

function Invoke-DisableStartup {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host "No scheduled task named '$TaskName'."; return }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the scheduled task '$TaskName'."
}

function Invoke-Uninstall {
    $summary = @"
  * stop the companion if this script started it
  * remove the '$TaskName' scheduled task if present
  * delete the virtual environment at $VenvPath
  * delete the backup environment at $VenvBackup
"@
    if ($Purge) {
        $summary += @"
  * -Purge given, so ALSO delete:
      - your configuration at $EnvFile
      - the executed-action ledger at $StateFile
      - the logs at $LogDir
"@
    } else {
        $summary += @"

It will KEEP your configuration ($EnvFile), the executed-action ledger, and the
logs -- those are yours. Pass -Purge to remove them too.
"@
    }
    if (-not (Confirm-Change $summary)) { Write-Host 'Nothing was changed.'; return }

    Invoke-Stop
    Invoke-DisableStartup
    foreach ($path in @($VenvPath, $VenvBackup)) {
        if (Test-Path $path) { Remove-Item $path -Recurse -Force }
    }
    if ($Purge) {
        foreach ($path in @($EnvFile, $StateFile, $PidFile, $LogDir)) {
            if (Test-Path $path) { Remove-Item $path -Recurse -Force }
        }
        if ((Test-Path $Root) -and -not (Get-ChildItem $Root)) { Remove-Item $Root -Force }
    }
    Write-Host 'Uninstalled.'
    Write-Host 'Nothing outside %LOCALAPPDATA%\Bartholomew was ever created, so nothing'
    Write-Host 'outside it needs cleaning up.'
}

function Invoke-Rollback {
    if (-not (Test-Path $VenvBackup)) {
        throw (
            "No previous environment at $VenvBackup. The 'install' verb takes that " +
            "backup, so there is nothing to roll back to yet."
        )
    }
    $summary = @"
  * stop the companion if this script started it
  * replace $VenvPath with the backup at $VenvBackup
  * leave your configuration and the executed-action ledger untouched
"@
    if (-not (Confirm-Change $summary)) { Write-Host 'Nothing was changed.'; return }

    Invoke-Stop
    if (Test-Path $VenvPath) { Remove-Item $VenvPath -Recurse -Force }
    Move-Item $VenvBackup $VenvPath
    Write-Host 'Rolled back to the previous environment.'
    Write-Host 'Check it with: .\bartholomew-action.ps1 diagnostics'
}

# --- inert until a verb is named ------------------------------------------------

if (-not $Verb) {
    Get-Help $PSCommandPath -Detailed
    Write-Host ''
    Write-Host 'No verb given, so nothing was changed.' -ForegroundColor Yellow
    exit 0
}

switch ($Verb) {
    'install'         { Invoke-Install }
    'start'           { Invoke-Start }
    'stop'            { Invoke-Stop }
    'status'          { Invoke-Status }
    'diagnostics'     { Invoke-Diagnostics }
    'enable-startup'  { Invoke-EnableStartup }
    'disable-startup' { Invoke-DisableStartup }
    'uninstall'       { Invoke-Uninstall }
    'rollback'        { Invoke-Rollback }
}
