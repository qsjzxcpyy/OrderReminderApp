Option Explicit

Dim shell, fileSystem, appDir, launcher, browserUrl, pythonExe, chromePaths, chromePath, index
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

appDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = appDir & "\start_hidden.vbs"
browserUrl = "http://127.0.0.1:8791"
pythonExe = appDir & "\.venv\Scripts\python.exe"
If Not fileSystem.FileExists(pythonExe) Then pythonExe = fileSystem.GetParentFolderName(appDir) & "\.venv\Scripts\python.exe"
If Not fileSystem.FileExists(pythonExe) Then pythonExe = "python.exe"

InstallAutoStart shell, pythonExe, launcher, appDir
If Not ServiceIsRunning(browserUrl) Then
    shell.Run Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & appDir & "\run_app.py" & Chr(34), 0, False
    WaitForService browserUrl, 30
End If

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

Function ServiceIsRunning(url)
    Dim request
    ServiceIsRunning = False
    On Error Resume Next
    Set request = CreateObject("WinHttp.WinHttpRequest.5.1")
    request.Open "GET", url & "/api/health", False
    request.SetTimeouts 500, 500, 1000, 1000
    request.Send
    If Err.Number = 0 Then ServiceIsRunning = (request.Status = 200)
    Err.Clear
    On Error GoTo 0
End Function

Sub WaitForService(url, attempts)
    Dim attempt
    For attempt = 1 To attempts
        If ServiceIsRunning(url) Then Exit Sub
        WScript.Sleep 500
    Next
End Sub

Sub InstallAutoStart(shellObject, pythonPath, launcherPath, workingDirectory)
    Dim startupDirectory, shortcutPath, shortcut
    On Error Resume Next
    startupDirectory = shellObject.SpecialFolders("Startup")
    If startupDirectory <> "" Then
        shortcutPath = startupDirectory & "\OrderReminderService.lnk"
        Set shortcut = shellObject.CreateShortcut(shortcutPath)
        shortcut.TargetPath = shellObject.ExpandEnvironmentStrings("%WINDIR%") & "\System32\wscript.exe"
        shortcut.Arguments = Chr(34) & launcherPath & Chr(34)
        shortcut.WorkingDirectory = workingDirectory
        shortcut.WindowStyle = 7
        shortcut.Description = "Order Reminder background service"
        shortcut.Save
    End If
    On Error GoTo 0
End Sub
