#requires -Version 5.1
<#
.SYNOPSIS
  生成本机配置文件 config\.env（已加入 .gitignore，不会被提交）。

.DESCRIPTION
  交互式询问学号、统一认证密码、通知渠道，写入 UTF-8 无 BOM 的 .env。
  密码输入不回显。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_setup.ps1
#>

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$configDir = Join-Path $root 'config'
$envPath = Join-Path $configDir '.env'

if (Test-Path $envPath) {
    $answer = Read-Host 'config\.env 已存在，是否覆盖？(y/N)'
    if ($answer -ne 'y') {
        Write-Host '已取消，未做任何修改。'
        exit 0
    }
}

$username = Read-Host '学号'
if ([string]::IsNullOrWhiteSpace($username)) {
    Write-Host '学号不能为空。'
    exit 1
}

$secure = Read-Host '智慧法大统一认证密码' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if ([string]::IsNullOrEmpty($password)) {
    Write-Host '密码不能为空。'
    exit 1
}

$strong = Read-Host '新增成绩时用强提醒（置顶弹窗 + 闪烁任务栏 + 提示音）？(Y/n)'
$strongFlag = '1'
if ($strong -eq 'n' -or $strong -eq 'N') {
    $strongFlag = '0'
}

$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add('# 由 local_setup.ps1 生成，请勿提交到仓库')
$lines.Add("CUPL_USERNAME=$username")
$lines.Add("CUPL_PASSWORD=$password")
$lines.Add('AUTH_MODE=tpass')
$lines.Add('NOTIFY_CHANNEL=desktop')
$lines.Add("NOTIFY_STRONG=$strongFlag")

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
[IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ''
Write-Host "已写入 $envPath"
Write-Host '下一步：'
Write-Host '  python src\monitor.py --dry-run     # 只验证登录与解析'
Write-Host '  python src\monitor.py --test-notify # 测试通知渠道'
Write-Host '  python src\monitor.py               # 正式跑一次'
Write-Host ''
Write-Host '想让它每 10 分钟自动检查（不依赖任何窗口）：'
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action install -IntervalMinutes 10'
