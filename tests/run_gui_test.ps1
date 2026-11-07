# Starts a real, interactive Maya against sandboxes that look like a finished install,
# and waits for tests/maya_gui_test.py to report.  Your own Maya prefs are not touched:
# MAYA_APP_DIR points into the sandbox.  Two runs, the two ways autoload gets switched on:
#
#   1. Brand new prefs: the module's bootstrap must switch autoload on, without any
#      dialog holding up startup.  Runs the full test.
#   2. Existing prefs, bootstrap removed: the exact line the installer writes to
#      pluginPrefs.mel must do it alone.
#
#   powershell -File tests/run_gui_test.ps1 -Sandbox D:\tmp\itest_gui
param(
    [string]$Maya = "C:\Program Files\Autodesk\Maya2024",
    [Parameter(Mandatory = $true)][string]$Sandbox,
    [int]$TimeoutSeconds = 300
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$versionName = (Split-Path $Maya -Leaf) -replace "^Maya", ""

if (Test-Path $Sandbox) { Remove-Item $Sandbox -Recurse -Force }
$appDir = Join-Path $Sandbox "itest_gui_appdir"
$modules = Join-Path $appDir "$versionName\modules"
$prefs = Join-Path $appDir "$versionName\prefs"
$shots = Join-Path $Sandbox "shots"
New-Item -ItemType Directory -Force $modules, $shots | Out-Null

python (Join-Path $root "installer\build.py") --stage-only
Copy-Item (Join-Path $root "build\stage\MayaShield") (Join-Path $modules "MayaShield") -Recurse
Set-Content (Join-Path $modules "mayashield.mod") "+ mayashield 4.0.0 ./MayaShield" -Encoding Ascii

# A real install has removed any old copy from bin/plug-ins.  This machine still has
# one, which outranks modules, so list the new plug-in folder first.
$env:MAYA_PLUG_IN_PATH = Join-Path $modules "MayaShield\plug-ins"
$env:MAYA_APP_DIR = $appDir
$env:MAYA_DISABLE_CIP = "1"; $env:MAYA_DISABLE_CER = "1"; $env:MAYA_DISABLE_ADP = "1"
$env:MAYA_NO_HOME = "1"; $env:MAYA_CM_DISABLE_ERROR_POPUPS = "1"
$env:MAYASHIELD_GUI_SHOTS = $shots

$startup = Join-Path $Sandbox "start.mel"
$testScript = (Join-Path $root "tests\maya_gui_test.py").Replace("\", "/")
Set-Content $startup ("python(""exec(compile(open(r'$testScript', encoding='utf-8').read(), r'$testScript', 'exec'))"");") -Encoding Ascii

function Invoke-MayaRun($label, $scene = $null) {
    $result = Join-Path $Sandbox "result_$label.json"
    $env:MAYASHIELD_GUI_RESULT = $result
    $log = Join-Path $Sandbox "maya_$label.txt"
    $arguments = @("-script", """$startup""", "-log", """$log""")
    if ($scene) { $arguments += @("-file", """$scene""") }
    $process = Start-Process (Join-Path $Maya "bin\maya.exe") -ArgumentList $arguments -PassThru -WorkingDirectory $Sandbox
    $started = Get-Date
    $deadline = $started.AddSeconds($TimeoutSeconds)
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    if (-not $process.HasExited) {
        Write-Host "TIMEOUT: Maya did not finish in $TimeoutSeconds s; stopping it."
        Stop-Process -Id $process.Id -Force
    }
    Write-Host ("[$label] Maya ran for {0:N0} s" -f ((Get-Date) - $started).TotalSeconds)
    if (-not (Test-Path $result)) {
        Write-Host "NO RESULT. Tail of Maya's log:"
        if (Test-Path $log) { Get-Content $log -Tail 30 | Write-Host }
        return 1
    }
    $report = Get-Content $result -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host ("Maya {0} | {1}" -f $report.maya, ($report.python -split " ")[0])
    $failed = 0
    foreach ($item in $report.results) {
        if (-not $item.ok) { $failed++ }
        Write-Host ("{0}  {1}  {2}" -f $(if ($item.ok) { "PASS" } else { "FAIL" }), $item.name, $item.detail)
    }
    Write-Host ("[$label] {0} checks, {1} failed" -f $report.results.Count, $failed)
    return $failed
}

$pluginPrefs = Join-Path $prefs "pluginPrefs.mel"
$userPrefs = Join-Path $prefs "userPrefs.mel"
$installerLine = 'evalDeferred("autoLoadPlugin(\"\", \"mayashield.py\", \"mayashield\")");'

function Remove-AutoloadTraces {
    $lines = @(Get-Content $pluginPrefs | Where-Object { $_ -notmatch "mayashield" })
    Set-Content $pluginPrefs $lines -Encoding Ascii
    Set-Content $userPrefs (@(Get-Content $userPrefs | Where-Object { $_ -notmatch "mayashield_autoloadConfigured" })) -Encoding Default
}

Write-Host "===== Run 1: brand new prefs, bootstrap only ====="
$failedTotal = [int](Invoke-MayaRun "fresh")
Write-Host "--- pluginPrefs.mel after Maya rewrote it on exit ---"
if (Test-Path $pluginPrefs) {
    Select-String -Path $pluginPrefs -Pattern "mayashield" | ForEach-Object { Write-Host $_.Line }
    Write-Host ("autoload entries kept: " + (Select-String -Path $pluginPrefs -Pattern "autoLoadPlugin").Count)
}

$bootstrap = Join-Path $modules "MayaShield\scripts\userSetup.py"
$bootstrapText = Get-Content $bootstrap -Raw

Write-Host "===== Run 2: existing prefs, installer's pluginPrefs line only ====="
Remove-Item $bootstrap -Force
Remove-AutoloadTraces
Add-Content $pluginPrefs $installerLine -Encoding Ascii
$env:MAYASHIELD_GUI_QUICK = "1"
$failedTotal += [int](Invoke-MayaRun "existing")
Write-Host "--- pluginPrefs.mel after second exit ---"
Select-String -Path $pluginPrefs -Pattern "mayashield" | ForEach-Object { Write-Host $_.Line }

# The everyday upgrade: a user who already ran Maya gets the bootstrap for the first
# time.  Maya hash-checks userSetup.py files, so this must not stop startup.
Write-Host "===== Run 3: existing prefs meet the bootstrap for the first time ====="
Remove-AutoloadTraces
Set-Content $bootstrap $bootstrapText -Encoding Ascii -NoNewline
$failedTotal += [int](Invoke-MayaRun "first_bootstrap")
Select-String -Path $pluginPrefs -Pattern "mayashield" | ForEach-Object { Write-Host $_.Line }

# The user switched autoload off: the bootstrap has to respect that.
Write-Host "===== Run 4: user turned autoload off, bootstrap must not turn it back on ====="
$lines = @(Get-Content $pluginPrefs | Where-Object { $_ -notmatch "mayashield" })
Set-Content $pluginPrefs $lines -Encoding Ascii
$env:MAYASHIELD_GUI_EXPECT_OFF = "1"
$failedTotal += [int](Invoke-MayaRun "user_off")
Remove-Item Env:MAYASHIELD_GUI_QUICK, Env:MAYASHIELD_GUI_EXPECT_OFF

# The way scenes usually arrive: double-clicked in Explorer, which starts Maya with the
# file as its argument.  The plug-in has to be up before Maya opens it.
Write-Host "===== Run 5: Maya started with an infected scene, as from a double-click ====="
Add-Content $pluginPrefs $installerLine -Encoding Ascii
$payloadMark = (Join-Path $Sandbox "payload_ran.txt").Replace("\", "/")
$dialogMark = (Join-Path $Sandbox "dialog_shown.txt").Replace("\", "/")
$infected = Join-Path $Sandbox "double_clicked.ma"
Set-Content $infected @"
//Maya ASCII 2024 scene
requires maya "2024";
createNode script -n "vaccine_gene";
	setAttr ".b" -type "string" "python(\"open(r'$payloadMark','a').write('ran')\");";
	setAttr ".st" 1;
"@ -Encoding Ascii
# Answers the guard's dialog for the test, from a userSetup like any user's.
$userScripts = Join-Path $appDir "$versionName\scripts"
New-Item -ItemType Directory -Force $userScripts | Out-Null
Set-Content (Join-Path $userScripts "userSetup.py") @"
def _answer(*_args):
    open(r'$dialogMark', 'a').write('shown')
    return 'Block'
from mayashield_core import guard
guard._ask = _answer
"@ -Encoding Ascii
$env:MAYASHIELD_GUI_CMDLINE = "1"
$env:MAYASHIELD_GUI_DIALOG_MARK = $dialogMark
$env:MAYASHIELD_GUI_PAYLOAD_MARK = $payloadMark
$env:MAYASHIELD_GUI_DELAY = "35"
$failedTotal += [int](Invoke-MayaRun "double_click" $infected)
Remove-Item Env:MAYASHIELD_GUI_CMDLINE, Env:MAYASHIELD_GUI_DIALOG_MARK, Env:MAYASHIELD_GUI_PAYLOAD_MARK, Env:MAYASHIELD_GUI_DELAY

Write-Host "===== total failures: $failedTotal ====="
if ($failedTotal) { exit 1 }
