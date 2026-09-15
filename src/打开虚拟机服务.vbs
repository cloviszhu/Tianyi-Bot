Option Explicit
Dim sh, fs, runtime, project
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
project = fs.GetParentFolderName(fs.GetParentFolderName(WScript.ScriptFullName))
runtime = project & "\.venv\Scripts\pythonw.exe"
If Not fs.FileExists(runtime) Then
  MsgBox "Please complete the guest Python setup in README.md.", 48, "TianyiBot"
  WScript.Quit 1
End If
sh.Run Chr(34) & runtime & Chr(34) & " -m wechat_gallery_bot.management.guest_gui", 0, False
