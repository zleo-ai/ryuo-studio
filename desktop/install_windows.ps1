# 在 Windows 上装龙屏：两个后台计划任务（推送、管理器）+ 开始菜单「龙屏」快捷方式。不需要管理员权限。
#   powershell -ExecutionPolicy Bypass -File desktop\install_windows.ps1            装好，登录后自动开推送和管理器
#   powershell -ExecutionPolicy Bypass -File desktop\install_windows.ps1 -NoAuto    只装，点图标时才启动
#   powershell -ExecutionPolicy Bypass -File desktop\install_windows.ps1 -Uninstall 删掉任务和快捷方式（配置和视频留着）
# 还没在真 Windows 上跑过，碰到问题看 README 里「Windows」一节。
param([switch]$NoAuto, [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Names = @("pusher", "manager")
$Lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "龙屏.lnk"

if ($Uninstall) {
    foreach ($n in $Names) { Unregister-ScheduledTask -TaskName "RyuoStudio-$n" -Confirm:$false -ErrorAction SilentlyContinue }
    Remove-Item $Lnk -ErrorAction SilentlyContinue
    Write-Host "已删掉计划任务和快捷方式。"
    exit 0
}

# 找 Python 3.11 以上；后台用 pythonw（没有黑窗口）
$Py = $null
foreach ($cand in @("py -3", "python")) {
    try {
        $exe = & cmd /c "$cand -c ""import sys; assert sys.version_info >= (3, 11); print(sys.executable)"" 2>nul"
        if ($LASTEXITCODE -eq 0 -and $exe) { $Py = $exe.Trim(); break }
    } catch {}
}
if (-not $Py) { throw "没找到 Python 3.11 以上。先从 python.org 装一个，安装时勾上 Add to PATH。" }
$Pyw = Join-Path (Split-Path $Py) "pythonw.exe"
if (-not (Test-Path $Pyw)) { $Pyw = $Py }
Write-Host "Python：$Py"

# Windows 上连屏幕走 hidapi（Linux 走 hidraw，不需要它）
& $Py -m pip install --user --quiet hidapi
if ($LASTEXITCODE -ne 0) { throw "装 hidapi 没成功，看看网络或 pip 配置。" }

# 依赖检查：缺了只提醒，不替你下载
if (-not (Get-Command adb -ErrorAction SilentlyContinue) -and -not $env:RYUO_ADB) {
    Write-Warning "没找到 adb：去 developer.android.com 下载 Android SDK Platform-Tools，解压后把目录加进 PATH（或设 RYUO_ADB 指到 adb.exe）。"
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "没找到 ffmpeg：只有「生成视频」要用，别的不受影响。"
}
try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:8085/data.json" | Out-Null }
catch { Write-Warning "读不到 CPU 温度：装 LibreHardwareMonitor，在 Options 里打开 Remote Web Server（端口 8085），并让它开机启动。不装的话温度显示破折号。" }

# 计划任务：登录时启动，退出了由 run_service.py 自己 10 秒后拉起；-NoAuto 时不设登录触发，只在点图标时启动
foreach ($n in $Names) {
    $act = New-ScheduledTaskAction -Execute $Pyw -Argument "-X utf8 -I ""$Root\tools\run_service.py"" $n" -WorkingDirectory $Root
    $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $reg = @{ TaskName = "RyuoStudio-$n"; Action = $act; Settings = $set; Description = "龙屏 $n"; Force = $true }
    if (-not $NoAuto) { $reg.Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME" }
    Register-ScheduledTask @reg | Out-Null
}

# 开始菜单快捷方式：点它确保服务在跑，再开独立窗口
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($Lnk)
$sh.TargetPath = $Pyw
$sh.Arguments = "-X utf8 -I ""$Root\tools\launch.py"""
$sh.WorkingDirectory = $Root
$sh.IconLocation = "$Root\desktop\ryuo-studio.ico"
$sh.Description = "龙屏管理器"
$sh.Save()

if (-not $NoAuto) { foreach ($n in $Names) { Start-ScheduledTask -TaskName "RyuoStudio-$n" } }
Write-Host "装好了：开始菜单里搜「龙屏」，右键可固定到任务栏。"
