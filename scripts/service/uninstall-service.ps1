# 卸载 HomeLink 开机自启（需要管理员权限）。
#
#   powershell -ExecutionPolicy Bypass -File scripts\service\uninstall-service.ps1
#
# 停止并删除 HomeLinkService（开机启动）、HomeLinkAgent（常驻 Agent 服务）、
# HomeLinkBackup（每日备份）、HomeLinkEval（每周评测）与 HomeLinkAddresses（登录后显示地址）。
# 旧项目任务 JJButlerService 默认保持停用；加 -RestoreOldService 可重新启用它。

[CmdletBinding()]
param(
    [switch]$RestoreOldService
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$logsDir = Join-Path $repoRoot 'logs'
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
Start-Transcript -Path (Join-Path $logsDir 'service-install.log') -Append | Out-Null

$oldTaskName = 'JJButlerService'
$serviceTaskName = 'HomeLinkService'
$agentTaskName = 'HomeLinkAgent'
$backupTaskName = 'HomeLinkBackup'
$evalTaskName = 'HomeLinkEval'
$addressTaskName = 'HomeLinkAddresses'

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '需要管理员权限：请用「以管理员身份运行」的 PowerShell 执行本脚本。'
    }
}

try {
    Assert-Admin

    foreach ($name in @($serviceTaskName, $agentTaskName, $backupTaskName, $evalTaskName, $addressTaskName)) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($null -eq $task) {
            Write-Host "未找到任务 $name，跳过。"
            continue
        }
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "已停止并删除 $name。"
    }

    if ($RestoreOldService) {
        $old = Get-ScheduledTask -TaskName $oldTaskName -ErrorAction SilentlyContinue
        if ($null -ne $old) {
            Enable-ScheduledTask -TaskName $oldTaskName | Out-Null
            Write-Host "已重新启用旧任务 $oldTaskName（登录后生效）。"
        } else {
            Write-Host "未找到旧任务 $oldTaskName，无法恢复。"
        }
    }

    Write-Host ''
    Write-Host '卸载完成。'
    exit 0
} catch {
    Write-Host ''
    Write-Host "卸载失败：$($_.Exception.Message)"
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
