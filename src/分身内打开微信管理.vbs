Option Explicit
Dim sh, fs, runtime
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
runtime = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.tianyi-bot\host-runtime\Scripts\pythonw.exe"
If Not fs.FileExists(runtime) Then
  MsgBox "TianyiBot runtime is missing.", 48, "TianyiBot"
  WScript.Quit 1
End If
sh.Run Chr(34) & runtime & Chr(34) & " -m wechat_gallery_bot.management.child_binding", 0, False
