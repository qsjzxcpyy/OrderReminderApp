Option Explicit

Dim shell, fileSystem, appDir, pythonExe, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
appDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonExe = appDir & "\.venv\Scripts\python.exe"
If Not fileSystem.FileExists(pythonExe) Then pythonExe = fileSystem.GetParentFolderName(appDir) & "\.venv\Scripts\python.exe"
If Not fileSystem.FileExists(pythonExe) Then pythonExe = "python.exe"
command = """" & pythonExe & """ """ & appDir & "\run_app.py"""
shell.CurrentDirectory = appDir
shell.Run command, 0, False
