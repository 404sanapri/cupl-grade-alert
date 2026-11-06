#requires -Version 5.1
<#
.SYNOPSIS
  计划任务调用的包装脚本：跑一次成绩检查，输出落到 logs\task-日期.log。

.DESCRIPTION
  由 scripts\local_task.ps1 注册的计划任务调用，一般不需要手工执行。
  自动挑选 Python（.venv > python > py），并把 14 天前的日志清掉。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_monitor.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_monitor.ps1 -DryRun
#>

param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$logDir = Join-Path $root 'logs'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# 让 Python 一律输出 UTF-8，PowerShell 也按 UTF-8 解码，否则中文日志会乱码
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
$logFile = Join-Path $logDir ('task-{0}.log' -f (Get-Date -Format 'yyyyMMdd'))

function Write-Log([string]$Text) {
    Add-Content -LiteralPath $logFile -Value $Text -Encoding UTF8
}

Get-ChildItem -LiteralPath $logDir -Filter 'task-*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$exe = $null
$prefix = @()

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $exe = $venvPython
}
else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $exe = $cmd.Source
    }
    else {
        $py = Get-Command py -ErrorAction SilentlyContinue
        if ($py) {
            $exe = $py.Source
            $prefix = @('-3')
        }
    }
}

if (-not $exe) {
    Write-Log ('[{0}] 找不到 python，请安装 Python 3 并加入 PATH' -f (Get-Date -Format 's'))
    exit 127
}

$target = Join-Path $root 'src\scheduled_run.py'
if (-not (Test-Path -LiteralPath $target)) {
    Write-Log ('[{0}] 找不到 {1}' -f (Get-Date -Format 's'), $target)
    exit 126
}

$argList = @()
$argList += $prefix
$argList += $target
if ($DryRun) { $argList += '--dry-run' }

Write-Log ('[{0}] ---- 开始 python={1}' -f (Get-Date -Format 's'), $exe)

$code = 1
# 管道里混入 stderr 时，$ErrorActionPreference='Stop' 会把普通错误行升级成终止错误，
# 连带把子进程打断（锁文件来不及释放），所以这一段临时放宽为 Continue。
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $exe @argList *>&1 | ForEach-Object { Write-Log ([string]$_) }
    $code = $LASTEXITCODE
}
catch {
    Write-Log ('[{0}] 异常：{1}' -f (Get-Date -Format 's'), $_.Exception.Message)
    $code = 1
}
finally {
    $ErrorActionPreference = $previousPreference
}

Write-Log ('[{0}] ---- 结束 exit={1}' -f (Get-Date -Format 's'), $code)
exit $code
