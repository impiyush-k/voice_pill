$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\VoicePill.lnk")
$Shortcut.TargetPath = "e:\project\june\real_things\voice_pill\start.bat"
$Shortcut.WorkingDirectory = "e:\project\june\real_things\voice_pill"
$Shortcut.WindowStyle = 7
$Shortcut.Save()
