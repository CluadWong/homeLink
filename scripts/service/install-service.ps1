# 安装 JJ 管家开机自启（需要管理员权限）。
#
#   powershell -ExecutionPolicy Bypass -File scripts\service\install-service.ps1
#
# 做三件事：
#   1. 停用旧项目的计划任务 JJButlerService（只停用不删除；-KeepOldService 可跳过）；
#   2. 注册 JJButler2Service：开机启动、后台静默，输出写 logs\service.log；
#   3. 注册 JJButler2Addresses：用户登录后弹出终端显示访问地址。
#
# 卸载见 uninstall-service.ps1。日志写 logs\service-install.log。

[CmdletBinding()]
param(
    [switch]$KeepOldService,
    [switch]$SkipStart
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$logsDir = Join-Path $repoRoot 'logs'
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
Start-Transcript -Path (Join-Path $logsDir 'service-install.log') -Append | Out-Null

$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$oldTaskName = 'JJButlerService'
$serviceTaskName = 'JJButler2Service'
$addressTaskName = 'JJButler2Addresses'

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '需要管理员权限：请用「以管理员身份运行」的 PowerShell 执行本脚本。'
    }
}

function Test-PortListening {
    param([int]$Port, [int]$TimeoutMilliseconds = 500)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return ($task.Wait($TimeoutMilliseconds) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Wait-PortState {
    param([int]$Port, [int]$TimeoutSeconds, [bool]$Listening)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ((Test-PortListening -Port $Port) -eq $Listening) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Stop-OldProjectProcesses {
    # 旧任务已停用但子进程可能残留（旧项目以新进程组启动），只清理命令行走旧仓库的进程。
    $oldPrefix = ((Split-Path $repoRoot -Parent) + '\JJ-Butler\').ToLowerInvariant()
    $newPrefix = ($repoRoot.TrimEnd('\') + '\').ToLowerInvariant()
    $killed = 0
    Get-CimInstance Win32_Process | ForEach-Object {
        $commandLine = "$($_.CommandLine)".ToLowerInvariant()
        if ($commandLine.Contains($oldPrefix) -and -not $commandLine.Contains($newPrefix)) {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "已结束旧项目残留进程 PID $($_.ProcessId)"
            $killed++
        }
    }
    return $killed
}

try {
    Assert-Admin

    $runService = Join-Path $repoRoot 'scripts\service\run-service.cmd'
    $showAddresses = Join-Path $repoRoot 'scripts\service\show-addresses.cmd'
    $python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    foreach ($required in @($runService, $showAddresses, $python)) {
        if (-not (Test-Path $required)) { throw "缺少必需文件：$required" }
    }

    # 1) 旧项目任务：只停用不删除，避免覆盖用户可回退的选择
    if (-not $KeepOldService) {
        $old = Get-ScheduledTask -TaskName $oldTaskName -ErrorAction SilentlyContinue
        if ($null -ne $old) {
            Write-Host "停用旧任务 $oldTaskName（保留任务，可随时启用）…"
            Stop-ScheduledTask -TaskName $oldTaskName -ErrorAction SilentlyContinue
            Disable-ScheduledTask -TaskName $oldTaskName | Out-Null
        } else {
            Write-Host "未找到旧任务 $oldTaskName，跳过。"
        }

        if (-not (Wait-PortState -Port 8000 -TimeoutSeconds 10 -Listening $false)) {
            Write-Host '8000 端口仍被占用，检查旧项目残留进程…'
            if ((Stop-OldProjectProcesses) -eq 0) {
                Write-Host '没有找到旧项目进程，端口可能被其他程序占用。'
            }
        }
    }

    # 2) 清理上次安装可能残留的任务与进程（例如被旧编码脚本卡住的 cmd）
    foreach ($name in @($serviceTaskName, $addressTaskName)) {
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
        }
    }
    $stale = Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and (
            $_.CommandLine -like '*run-service.cmd*' -or
            $_.CommandLine -like '*scripts\start.py*' -or
            $_.CommandLine -like '*jj_butler.interface.api.app*'
        )
    }
    foreach ($proc in $stale) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "已结束残留进程 PID $($proc.ProcessId)"
    }
    $serviceLog = Join-Path $logsDir 'service.log'
    if (Test-Path $serviceLog) { Clear-Content -Path $serviceLog -ErrorAction SilentlyContinue }

    # 3) 开机静默启动：SYSTEM 账号，不需要登录
    $serviceAction = New-ScheduledTaskAction -Execute $runService -WorkingDirectory $repoRoot
    $serviceTrigger = New-ScheduledTaskTrigger -AtStartup
    $servicePrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $serviceSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask `
        -TaskName $serviceTaskName `
        -Action $serviceAction `
        -Trigger $serviceTrigger `
        -Principal $servicePrincipal `
        -Settings $serviceSettings `
        -Description 'JJ 管家（重建版）：开机后台启动 uvicorn，日志 logs\service.log' `
        -Force | Out-Null
    if ($null -eq (Get-ScheduledTask -TaskName $serviceTaskName -ErrorAction SilentlyContinue)) {
        throw "任务 $serviceTaskName 注册失败（任务列表里找不到）。"
    }
    Write-Host "已注册 $serviceTaskName（开机启动，后台静默）。"

    # 4) 登录后显示地址：只读展示，不重复启动服务
    $addressAction = New-ScheduledTaskAction -Execute $showAddresses -WorkingDirectory $repoRoot
    $addressTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
    $addressPrincipal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
    $addressSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $addressTaskName `
        -Action $addressAction `
        -Trigger $addressTrigger `
        -Principal $addressPrincipal `
        -Settings $addressSettings `
        -Description 'JJ 管家（重建版）：登录后弹出终端显示访问地址' `
        -Force | Out-Null
    Write-Host "已注册 $addressTaskName（登录后显示访问地址）。"

    # 5) 立即启动一次，方便当场看到地址；不再依赖注销重登
    if (-not $SkipStart) {
        if (-not (Wait-PortState -Port 8000 -TimeoutSeconds 15 -Listening $false)) {
            Write-Host '警告：8000 端口仍被占用，新服务可能启动失败，请查看 logs\service.log。'
        }
        Start-ScheduledTask -TaskName $serviceTaskName
        if (Wait-PortState -Port 8000 -TimeoutSeconds 60 -Listening $true) {
            Write-Host '服务已在 8000 端口监听。'
        } else {
            Write-Host '警告：60 秒内没等到 8000 端口，请查看 logs\service.log。'
        }
    }

    Write-Host ''
    Write-Host '安装完成。'
    Write-Host "  服务日志：$logsDir\service.log"
    Write-Host "  地址查看：scripts\service\show-addresses.cmd"
    exit 0
} catch {
    Write-Host ''
    Write-Host "安装失败：$($_.Exception.Message)"
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
