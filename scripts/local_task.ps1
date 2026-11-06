#requires -Version 5.1
<#
.SYNOPSIS
  管理「法大成绩提醒」的 Windows 计划任务（代替 GitHub Actions 定时跑）。

.DESCRIPTION
  教务系统 jwxt.cupl.edu.cn:80 对境外 IP 返回超时，GitHub Actions 的 runner 全部在境外，
  因此定时检查改在本机跑。本脚本负责注册 / 查看 / 立即运行 / 卸载这个计划任务。

  计划任务调用 scripts\run_monitor.ps1，后者再调 src\scheduled_run.py；
  每次运行都会追加一行到 logs\runs.jsonl，网页界面的「运行记录」里能看到。

.PARAMETER Action
  install 注册（或覆盖）计划任务；remove 卸载；status 查看状态；run 立即运行一次。

.PARAMETER IntervalMinutes
  检查间隔，5～120 分钟，默认 10。

.PARAMETER TaskName
  任务名，默认「法大成绩提醒」。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action install -IntervalMinutes 10

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action status
#>

param(
    [ValidateSet('install', 'remove', 'status', 'run')]
    [string]$Action = 'status',
    [int]$IntervalMinutes = 10,
    [string]$TaskName = '法大成绩提醒'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root 'scripts\run_monitor.ps1'
$envFile = Join-Path $root 'config\.env'

function Test-TaskModule {
    return [bool](Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)
}

function Test-TaskExists {
    if (Test-TaskModule) {
        return [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
    }
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & schtasks.exe /query /tn "$TaskName" 2>&1 | Out-Null
    $exists = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $previousPreference
    return $exists
}

function Register-MyTask {
    $argLine = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $runner
    $desc = '定时检查中国政法大学教务系统成绩，有新增科目时推送钉钉/飞书'

    if (Test-TaskModule) {
        try {
            $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argLine
            $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
                -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
            Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger `
                -Settings $settings -Description $desc -Force | Out-Null
            return 'ScheduledTasks'
        }
        catch {
            Write-Host ("Register-ScheduledTask 失败：{0}" -f $_.Exception.Message) -ForegroundColor Yellow
            Write-Host '改为尝试 schtasks.exe …'
        }
    }

    $tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $runner
    & schtasks.exe /create /tn "$TaskName" /tr $tr /sc minute /mo $IntervalMinutes /f | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'schtasks 创建失败，可能需要以管理员身份运行 PowerShell。'
    }
    return 'schtasks'
}

function Show-Status {
    if (Test-TaskModule) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $TaskName
            Write-Host ("任务：{0}" -f $TaskName)
            Write-Host ("状态：{0}" -f $task.State)
            Write-Host ("上次运行：{0}  结果：0x{1:X}" -f $info.LastRunTime, $info.LastTaskResult)
            Write-Host ("下次运行：{0}" -f $info.NextRunTime)
            $logDir = Join-Path $root 'logs'
            if (Test-Path -LiteralPath $logDir) {
                Write-Host ("日志目录：{0}" -f $logDir)
            }
            return
        }
    }

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & schtasks.exe /query /tn "$TaskName" /v /fo LIST 2>&1
    $found = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $previousPreference

    if ($found) {
        $output
    }
    else {
        Write-Host ("任务「{0}」还没安装。" -f $TaskName)
        Write-Host '  安装：powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action install'
    }
}

switch ($Action) {
    'install' {
        if ($IntervalMinutes -lt 5 -or $IntervalMinutes -gt 120) {
            Write-Host '检查间隔必须在 5～120 分钟之间。'
            exit 2
        }
        if (-not (Test-Path -LiteralPath $runner)) {
            Write-Host ("缺少 {0}" -f $runner)
            exit 2
        }
        if (-not (Test-Path -LiteralPath $envFile)) {
            Write-Host '还没生成 config\.env，先跑一次：' -ForegroundColor Yellow
            Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_setup.ps1'
            exit 3
        }

        $mode = Register-MyTask
        Write-Host ("已注册计划任务「{0}」，每 {1} 分钟检查一次（{2}）" -f $TaskName, $IntervalMinutes, $mode)
        Write-Host ''
        Write-Host '接下来：'
        Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action run'
        Write-Host '      # 立刻跑一次，第一次只建立基线，不会推送'
        Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action status'
        Write-Host ''
        Write-Host '注意：任务只在当前用户登录后运行；笔记本合盖 / 关机期间不会检查，'
        Write-Host '      开机后 Task Scheduler 会补跑一次（StartWhenAvailable）。'
    }
    'remove' {
        if (-not (Test-TaskExists)) {
            Write-Host ("任务「{0}」本来就不存在，无需卸载。" -f $TaskName)
            exit 0
        }
        if (Test-TaskModule) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        else {
            & schtasks.exe /delete /tn "$TaskName" /f | Out-Null
        }
        Write-Host ("已卸载计划任务「{0}」" -f $TaskName)
    }
    'run' {
        if (-not (Test-TaskExists)) {
            Write-Host ("任务「{0}」还没安装，先执行 -Action install。" -f $TaskName)
            exit 3
        }
        if (Test-TaskModule) {
            Start-ScheduledTask -TaskName $TaskName
        }
        else {
            & schtasks.exe /run /tn "$TaskName" | Out-Null
        }
        Write-Host '已触发一次运行，几秒后执行：'
        Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action status'
        Write-Host ("  或直接看日志：{0}" -f (Join-Path $root ('logs\task-{0}.log' -f (Get-Date -Format 'yyyyMMdd'))))
    }
    default {
        Show-Status
    }
}
