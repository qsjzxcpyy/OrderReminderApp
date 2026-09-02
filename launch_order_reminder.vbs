Option Explicit

Dim shell, fileSystem, appDir, launcher, browserUrl, chromePaths, chromePath, index
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

appDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = appDir & "\start_hidden.vbs"
browserUrl = "http://127.0.0.1:8791"

shell.Run """" & launcher & """", 0, False
WScript.Sleep 1500

chromePaths = Array( _
    shell.ExpandEnvironmentStrings("%ProgramFiles%") & "\Google\Chrome\Application\chrome.exe", _
    shell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Google\Chrome\Application\chrome.exe", _
    shell.ExpandEnvironmentStrings("%LocalAppData%") & "\Google\Chrome\Application\chrome.exe" _
)
chromePath = ""
For index = 0 To UBound(chromePaths)
    If fileSystem.FileExists(chromePaths(index)) Then
        chromePath = chromePaths(index)
        Exit For
    End If
Next
If chromePath <> "" Then
    shell.Run """" & chromePath & """ --new-window " & browserUrl, 1, False
Else
    shell.Run browserUrl, 1, False
End If
