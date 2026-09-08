$WshShell = New-Object -comObject WScript.Shell
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Get-Location }
$ShortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\VoicePill.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$ScriptDir\start_silent.vbs"
$Shortcut.WorkingDirectory = $ScriptDir
$Shortcut.WindowStyle = 7
$Shortcut.Save()

