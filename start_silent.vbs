Set WshShell = CreateObject("WScript.Shell")
' Set the working directory to the project folder
WshShell.CurrentDirectory = "e:\project\june\real_things\voice_pill"

' Run main.py using the windowless python launcher (pyw)
' The '1' argument runs it normally (pyw already hides the console natively)
' The 'False' argument means we do not wait for the script to finish
WshShell.Run "pyw main.py", 1, False
