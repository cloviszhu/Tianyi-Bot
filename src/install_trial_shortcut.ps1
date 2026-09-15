param([Parameter(Mandatory=$true)][string]$TrialDirectory)
$ErrorActionPreference = 'Stop'
$trialRoot = (Resolve-Path -LiteralPath $TrialDirectory).Path
$launcherPath = Join-Path $trialRoot 'launch_child.pyw'
$manifest = Get-Content -LiteralPath (Join-Path $trialRoot 'release.json') -Raw | ConvertFrom-Json
if ($manifest.wheel -notmatch '^wechat_gallery_bot-[a-zA-Z0-9_.]+-py3-none-any\.whl$') { throw 'Invalid package name' }
$wheelPath = Join-Path $trialRoot $manifest.wheel
if ((Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256) { throw 'Package checksum mismatch' }
$runtimePath = Join-Path $env:USERPROFILE '.tianyi-bot\host-runtime\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $runtimePath) -or -not (Test-Path -LiteralPath $launcherPath)) { throw 'Missing runtime or launcher' }
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) '天意Bot试运行版.lnk'
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $runtimePath
$shortcut.Arguments = '"' + $launcherPath + '"'
$shortcut.WorkingDirectory = $trialRoot
$shortcut.Description = '在现有分身里打开试运行版；不重启微信，不自动收发。'
$shortcut.Save()
Get-Item -LiteralPath $shortcutPath | Select-Object FullName
