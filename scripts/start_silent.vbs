Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Set the working directory dynamically to the script's folder
ScriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = ScriptDir

' Run main.py using windowless python launcher
WshShell.Run "pyw main.py", 1, False

