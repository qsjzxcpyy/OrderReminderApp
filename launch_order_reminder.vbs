Option Explicit

Dim shell, fileSystem, appDir, launcher, browserUrl
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

appDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = appDir & "\start_hidden.vbs"
browserUrl = "http://127.0.0.1:8788"

shell.Run """" & launcher & """", 0, False
WScript.Sleep 1500
shell.Run browserUrl, 1, False
