# sync-plugin.ps1 —— 将 fluxiaRSS 插件同步到 everyday-rss vault
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/sync-plugin.ps1
#   可选：-VaultPluginDir <路径> 指定 vault 插件目录（默认自动定位）
#
# 步骤：
#   1) 在 fluxiaRSS 仓库 git pull origin master
#   2) 把 plugin/{main.js, styles.css, manifest.json} 复制到
#      <workspace>/everyday-rss/.obsidian/plugins/fluxiars-digest/ 覆盖旧文件
#   不覆盖 data.json（保留插件设置）。
# 注意：样式文件是 styles.css（不是 style.css）。

[CmdletBinding()]
param(
    [string]$VaultPluginDir = ""
)

$ErrorActionPreference = "Stop"

# 定位仓库根：本脚本位于 <fluxiaRSS>/scripts/
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $VaultPluginDir) {
    # vault 与 playground 平级，位于工作区根的 everyday-rss
    $Workspace = (Resolve-Path (Join-Path $RepoRoot "..\..")).Path
    $VaultPluginDir = Join-Path $Workspace "everyday-rss\.obsidian\plugins\fluxiars-digest"
}

Write-Host "==> 仓库: $RepoRoot"
Write-Host "==> 目标: $VaultPluginDir"

# 1) git pull
Push-Location $RepoRoot
try {
    Write-Host "==> git pull origin master ..."
    git pull origin master
    if ($LASTEXITCODE -ne 0) {
        throw "git pull 失败（exit=$LASTEXITCODE），中止同步"
    }
} finally {
    Pop-Location
}

# 2) 校验源文件与目标目录
$SrcDir = Join-Path $RepoRoot "plugin"
$Files = @("main.js", "styles.css", "manifest.json")
$Missing = @($Files | Where-Object { -not (Test-Path -LiteralPath (Join-Path $SrcDir $_)) })
if ($Missing.Count -gt 0) {
    throw "源文件缺失: $($Missing -join ', ')"
}
if (-not (Test-Path -LiteralPath $VaultPluginDir)) {
    New-Item -ItemType Directory -Path $VaultPluginDir -Force | Out-Null
    Write-Host "==> 已创建目标目录: $VaultPluginDir"
}

# 3) 复制覆盖（保留 data.json，不清除插件设置）
foreach ($f in $Files) {
    Copy-Item -LiteralPath (Join-Path $SrcDir $f) -Destination (Join-Path $VaultPluginDir $f) -Force
    Write-Host "==> 已复制 $f"
}

Write-Host "==> 完成，插件已同步到: $VaultPluginDir"
Write-Host "    在 Obsidian 中重载插件（设置→第三方插件→禁用再启用）以生效"
