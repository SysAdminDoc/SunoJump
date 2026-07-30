param(
    [string]$Python = "python",
    [int]$TimeoutSeconds = 40
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

Add-Type -AssemblyName UIAutomationClient
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class SunoJumpWindowFinder
{
    public delegate bool EnumWindowProc(IntPtr handle, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(
        EnumWindowProc callback,
        IntPtr parameter
    );

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(
        IntPtr handle,
        out uint processId
    );

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr handle);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(
        IntPtr handle,
        StringBuilder text,
        int maximum
    );

    public static IntPtr FindVisibleWindow(
        uint expectedProcessId,
        string titlePrefix
    )
    {
        IntPtr match = IntPtr.Zero;
        EnumWindows(
            delegate(IntPtr handle, IntPtr parameter)
            {
                uint processId;
                GetWindowThreadProcessId(handle, out processId);
                if (
                    processId != expectedProcessId
                    || !IsWindowVisible(handle)
                )
                {
                    return true;
                }
                StringBuilder title = new StringBuilder(512);
                GetWindowText(handle, title, title.Capacity);
                if (title.ToString().StartsWith(titlePrefix))
                {
                    match = handle;
                    return false;
                }
                return true;
            },
            IntPtr.Zero
        );
        return match;
    }
}
'@

$hostCode = @"
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, r"$repoRoot")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
import sunojump
state = tempfile.TemporaryDirectory(prefix="sunojump-uia-state-")
app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(sunojump.STYLE)
settings = QSettings(
    str(Path(state.name) / "session.ini"),
    QSettings.Format.IniFormat,
)
window = sunojump.MainWindow(settings=settings)
window.show()
exit_code = app.exec()
state.cleanup()
raise SystemExit(exit_code)
"@

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $Python
$startInfo.WorkingDirectory = $repoRoot
$startInfo.UseShellExecute = $false
$startInfo.ArgumentList.Add("-c")
$startInfo.ArgumentList.Add($hostCode)
$startInfo.Environment.Remove("QT_QPA_PLATFORM") | Out-Null
$startInfo.Environment["QT_ACCESSIBILITY"] = "1"
$process = [System.Diagnostics.Process]::Start($startInfo)

try {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $handle = [IntPtr]::Zero
    while (
        $handle -eq [IntPtr]::Zero -and
        [DateTime]::UtcNow -lt $deadline -and
        -not $process.HasExited
    ) {
        Start-Sleep -Milliseconds 200
        $handle = [SunoJumpWindowFinder]::FindVisibleWindow(
            [uint32]$process.Id,
            "SunoJump v"
        )
    }
    if ($handle -eq [IntPtr]::Zero) {
        throw "SunoJump accessibility window did not appear."
    }

    $window = [System.Windows.Automation.AutomationElement]::FromHandle(
        $handle
    )
    $all = $window.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )

    $byName = @{}
    for ($index = 0; $index -lt $all.Count; $index++) {
        $element = $all.Item($index)
        $name = $element.Current.Name
        if ($name -and -not $byName.ContainsKey($name)) {
            $byName[$name] = $element
        }
    }
    foreach ($requiredName in @(
        "Audio queue",
        "Browse audio files",
        "Process all",
        "Output directory",
        "Session log"
    )) {
        if (-not $byName.ContainsKey($requiredName)) {
            throw "Missing UI Automation element: $requiredName"
        }
    }

    $unitSliders = @{
        "Pitch Micro-Shift amount:" = " st"
        "Tempo Micro-Variation amount:" = "%"
        "Noise Injection amount:" = " dB"
        "Lossy Re-encode amount:" = " kbps"
    }
    foreach ($entry in $unitSliders.GetEnumerator()) {
        $match = $null
        for ($index = 0; $index -lt $all.Count; $index++) {
            $element = $all.Item($index)
            $name = $element.Current.Name
            if (
                $element.Current.ControlType -eq
                    [System.Windows.Automation.ControlType]::Slider -and
                $name.StartsWith($entry.Key) -and
                $name.EndsWith($entry.Value)
            ) {
                $match = $element
                break
            }
        }
        if ($null -eq $match) {
            throw "Missing unit-bearing UI Automation slider: $($entry.Key)"
        }
    }

    $queue = $byName["Audio queue"]
    if (-not $queue.Current.IsKeyboardFocusable) {
        throw "Audio queue is not keyboard focusable."
    }
    $queue.SetFocus()
    Start-Sleep -Milliseconds 200
    if (-not $queue.Current.HasKeyboardFocus) {
        throw "UI Automation could not focus the audio queue."
    }

    $summary = (
        "Windows UIA smoke passed: {0} descendants; keyboard focus and " +
        "unit-bearing slider names verified."
    ) -f $all.Count
    Write-Output $summary

    $windowPattern = $window.GetCurrentPattern(
        [System.Windows.Automation.WindowPattern]::Pattern
    )
    $windowPattern.Close()
    $process.WaitForExit(5000) | Out-Null
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
    $process.Dispose()
}
