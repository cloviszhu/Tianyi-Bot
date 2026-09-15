param([Parameter(Mandatory=$true)][string]$HostExecutable)
$ErrorActionPreference = 'Stop'
$resolvedHost = (Resolve-Path -LiteralPath $HostExecutable).Path
$runtimePath = Join-Path $env:USERPROFILE '.tianyi-bot\host-runtime\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $runtimePath)) { throw 'Missing runtime' }
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutShell = New-Object -ComObject WScript.Shell
$first = $shortcutShell.CreateShortcut((Join-Path $desktopPath '打开天意Bot分身.lnk'))
$first.TargetPath = $resolvedHost
$first.Arguments = '--open-child'
$first.WorkingDirectory = Split-Path $resolvedHost
$first.WindowStyle = 1
$first.IconLocation = $resolvedHost + ',0'
$first.Description = '创建本机分身；需要时由用户确认系统管理员提示。'
$first.Save()
$second = $shortcutShell.CreateShortcut((Join-Path $desktopPath '启动天意Bot.lnk'))
$second.TargetPath = $runtimePath
$second.Arguments = '-m wechat_gallery_bot.management.child_binding'
$second.WorkingDirectory = Split-Path $runtimePath
$second.Description = '在分身内登录微信后打开；主机误开拒绝连接。'
$second.Save()
Get-Item -LiteralPath (Join-Path $desktopPath '打开天意Bot分身.lnk'),(Join-Path $desktopPath '启动天意Bot.lnk') | Select-Object FullName
