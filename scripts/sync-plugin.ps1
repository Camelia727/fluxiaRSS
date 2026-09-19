# sync-plugin.ps1 —— 将 fluxiaRSS 插件同步到 everyday-rss vault
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/sync-plugin.ps1
#   可选参数：
#     -VaultPluginDir <路径>   指定目标插件目录（默认自动定位 everyday-rss）
#     -SkipPull                跳过 git pull（本地已是最新时用）
#     -Build                   同步前先 npm run build（改了 main.ts 时用）
#
# 步骤：
#   1) （默认）在 fluxiaRSS 仓库 git pull origin master
#   2) （可选）npm run build 重新生成 main.js
#   3) 把 plugin/{main.js, styles.css, manifest.json} 复制到目标目录并覆盖
#   4) 逐文件比对源/目标哈希，确认确实写入成功
#   不覆盖 data.json（保留插件设置、分区目录等用户配置）。
# 注意：样式文件名是 styles.css（不是 style.css）。

[CmdletBinding()]
param(
    [string]$VaultPluginDir = "",
    [switch]$SkipPull,
    [switch]$Build
)

$ErrorActionPreference = "Stop"

# 需要同步的文件（data.json 是用户配置，刻意排除）
$Files = @("main.js", "styles.css", "manifest.json")

function Get-FileHashSafe([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return "<missing>" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

# 定位仓库根：本脚本位于 <fluxiaRSS>/scripts/
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SrcDir = Join-Path $RepoRoot "plugin"

if (-not $VaultPluginDir) {
    # vault 与 playground 平级，位于工作区根的 everyday-rss
    $Workspace = (Resolve-Path (Join-Path $RepoRoot "..\..")).Path
    $VaultPluginDir = Join-Path $Workspace "everyday-rss\.obsidian\plugins\fluxiars-digest"
}

Write-Host "==> 仓库: $RepoRoot"
Write-Host "==> 目标: $VaultPluginDir"

# 1) git pull
if ($SkipPull) {
    Write-Host "==> 跳过 git pull（-SkipPull）"
} else {
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
}

# 2) 可选构建
if ($Build) {
    Push-Location $SrcDir
    try {
        Write-Host "==> npm run build ..."
        npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "npm run build 失败（exit=$LASTEXITCODE），中止同步"
        }
    } finally {
        Pop-Location
    }
}

# 3) 校验源文件与目标目录
$Missing = @($Files | Where-Object { -not (Test-Path -LiteralPath (Join-Path $SrcDir $_)) })
if ($Missing.Count -gt 0) {
    throw "源文件缺失: $($Missing -join ', ')"
}
if (-not (Test-Path -LiteralPath $VaultPluginDir)) {
    New-Item -ItemType Directory -Path $VaultPluginDir -Force | Out-Null
    Write-Host "==> 已创建目标目录: $VaultPluginDir"
}

# 4) 复制 + 逐文件哈希校验
Write-Host "==> 同步文件（保留 data.json）"
$Failed = @()
foreach ($f in $Files) {
    $src = Join-Path $SrcDir $f
    $dst = Join-Path $VaultPluginDir $f
    $before = Get-FileHashSafe $dst
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $srcHash = Get-FileHashSafe $src
    $dstHash = Get-FileHashSafe $dst
    $ok = ($srcHash -eq $dstHash)
    if (-not $ok) { $Failed += $f }
    $changed = if ($before -eq $dstHash) { "未变化" } else { "已更新" }
    $status = if ($ok) { "OK  " } else { "FAIL" }
    Write-Host ("  [{0}] {1,-14} {2}  ({3})" -f $status, $f, $changed, $dstHash.Substring(0, 12))
}

if ($Failed.Count -gt 0) {
    throw "以下文件校验不一致: $($Failed -join ', ')"
}

# 5) 确认 data.json 未被触碰
$dataPath = Join-Path $VaultPluginDir "data.json"
if (Test-Path -LiteralPath $dataPath) {
    Write-Host "==> data.json 已保留（插件设置未被覆盖）"
} else {
    Write-Host "==> 提示：目标目录暂无 data.json，插件首次启用时会生成默认设置"
}

Write-Host ""
Write-Host "==> 完成，插件已同步到: $VaultPluginDir"
Write-Host "    在 Obsidian 中重载插件生效：设置 → 第三方插件 → 关闭再开启（或 Ctrl+R 重载应用）"
Write-Host "    重载后到插件设置页点「🔄 拉取分区」，即可在「分区笔记目录」里为每个分区指定目录"
