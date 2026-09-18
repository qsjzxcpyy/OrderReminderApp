Option Explicit

Dim shell, fileSystem, appDir, pythonExe, command, index
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
appDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonExe = appDir & "\.venv\Scripts\python.exe"
If Not fileSystem.FileExists(pythonExe) Then pythonExe = fileSystem.GetParentFolderName(appDir) & "\.venv\Scripts\python.exe"
If Not fileSystem.FileExists(pythonExe) Then pythonExe = "python.exe"
command = Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & appDir & "\run_app.py" & Chr(34)
For index = 0 To WScript.Arguments.Count - 1
    command = command & " " & WScript.Arguments(index)
Next
shell.CurrentDirectory = appDir
shell.Run command, 0, False
