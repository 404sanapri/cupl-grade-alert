#requires -Version 5.1
<#
.SYNOPSIS
  把桌面版打包成单文件 exe（dist\法大成绩提醒.exe）。

.DESCRIPTION
  打包产物是「窗口版」：双击打开就自动开始监测，关掉窗口就停止。
  首次打开会弹出设置窗口，配置写入 exe 同目录的 config\.env。

  需要先装 PyInstaller：pip install pyinstaller

.PARAMETER Name
  exe 名字，默认「法大成绩提醒」。

.PARAMETER Console
  打带控制台的版本，排查启动问题时用（能看到 Python 报错）。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
#>

param(
    [string]$Name = '法大成绩提醒',
    [switch]$Console
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$env:PYTHONUTF8 = '1'

# PyInstaller 的进度信息走 stderr，这里必须放宽错误偏好，否则会被当成终止错误
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'

python -m PyInstaller --version 2>&1 | Out-Null
$hasPyi = ($LASTEXITCODE -eq 0)

if (-not $hasPyi) {
    Write-Host '没装 PyInstaller，正在安装 …' -ForegroundColor Yellow
    python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = $previousPreference
        throw 'PyInstaller 安装失败，请先手动执行：pip install pyinstaller'
    }
}

$mode = if ($Console) { '--console' } else { '--windowed' }

$pyiArgs = @(
    '--noconfirm',
    '--clean',
    '--onefile',
    $mode,
    '--name', $Name,
    '--distpath', 'dist',
    '--workpath', 'build\pyinstaller',
    '--specpath', 'build',
    '--paths', 'src',
    'app.py'
)

Write-Host ("打包中：{0}（{1}）" -f $Name, $mode) -ForegroundColor Cyan
python -m PyInstaller @pyiArgs
$buildCode = $LASTEXITCODE

$ErrorActionPreference = $previousPreference

if ($buildCode -ne 0) { throw 'PyInstaller 打包失败' }

$exe = Join-Path $root ("dist\{0}.exe" -f $Name)
if (-not (Test-Path -LiteralPath $exe)) { throw "没有找到产物：$exe" }

$size = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 1)
Write-Host ''
Write-Host ("完成：{0}（{1} MB）" -f $exe, $size) -ForegroundColor Green
Write-Host ''
Write-Host '用法：'
Write-Host '  1. 把 exe 放到项目根目录（和 config\、state\ 同级），配置就写在它旁边；'
Write-Host '  2. 双击运行 → 首次弹出设置窗口 → 填学号、密码、Webhook；'
Write-Host '  3. 之后每次双击都是「立即检查一次 + 每 N 分钟自动检查」，关掉窗口即停止。'
