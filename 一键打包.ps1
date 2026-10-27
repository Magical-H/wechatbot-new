#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$VenvDir = Join-Path $ProjectDir '.build-venv'
$Python = Join-Path $VenvDir 'Scripts\python.exe'
$OutputDir = Join-Path $ProjectDir 'dist\WeChatBot'
$ExePath = Join-Path $OutputDir 'WeChatBot.exe'

function Write-Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
try {
    Write-Host 'WeChatBot 单文件 EXE 一键打包' -ForegroundColor Green
    Write-Host 'EXE 内含 Python 运行时和程序依赖；配置、提示词及用户数据作为可编辑应用文件放在 EXE 同目录。'
    Write-Host '依赖默认从清华 PyPI 镜像下载；pip 会自动读取 Windows 代理设置。'

    if (-not (Test-Path $Python)) {
        $GlobalPython = Get-Command python -ErrorAction SilentlyContinue
        $BasePython = $null
        foreach ($Candidate in @('D:\Runtime\Python312\python.exe', 'D:\Runtime\Python\python.exe')) {
            if (Test-Path $Candidate) { $BasePython = $Candidate; break }
        }
        if (-not $BasePython -and $GlobalPython -and $GlobalPython.Source -notmatch '\\WindowsApps\\') {
            $BasePython = $GlobalPython.Source
        }
        if (-not $BasePython) {
            $BasePython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        }
        if (-not (Test-Path $BasePython)) { throw '未找到可用的全局 Python。请先安装 Python，打包虚拟环境会创建在项目目录。' }
        & $BasePython -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw '创建打包虚拟环境失败。' }
    }

    Write-Step '安装或更新项目打包依赖（仅安装到项目目录 .build-venv）'
    & $Python -m pip install --no-cache-dir --index-url 'https://pypi.tuna.tsinghua.edu.cn/simple' -r (Join-Path $ProjectDir 'requirements.txt') pyinstaller
    if ($LASTEXITCODE -ne 0) { throw '安装项目依赖或 PyInstaller 失败。' }

    Write-Step '清理本项目上次的 EXE 构建临时目录'
    $BuildDir = Join-Path $ProjectDir 'build\pyinstaller'
    if (Test-Path $BuildDir) { Remove-Item -LiteralPath $BuildDir -Recurse -Force }
    if (Test-Path $OutputDir) { Remove-Item -LiteralPath $OutputDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

    Write-Step '构建单文件 EXE'
    & $Python -m py_compile (Join-Path $ProjectDir 'single_launcher.py') (Join-Path $ProjectDir 'config_editor.py') (Join-Path $ProjectDir 'bot.py') (Join-Path $ProjectDir 'wxbot.py')
    if ($LASTEXITCODE -ne 0) { throw 'Python 源码语法检查失败。' }
    Push-Location $ProjectDir
    try {
        & $Python -m PyInstaller --noconfirm --clean --onefile --console `
            --name WeChatBot --distpath $OutputDir --workpath $BuildDir `
            --specpath $BuildDir --exclude-module config `
            --add-data "$(Join-Path $ProjectDir 'config.py');defaults" `
            --add-data "$(Join-Path $ProjectDir 'prompts');prompts" `
            --add-data "$(Join-Path $ProjectDir 'emojis');emojis" `
            --add-data "$(Join-Path $ProjectDir 'templates');templates" `
            --add-data "$(Join-Path $ProjectDir 'Demo_Image');Demo_Image" `
            --collect-all wechatauto --collect-all uiautomation `
            --hidden-import config_editor --hidden-import bot --hidden-import wxbot `
            --hidden-import winsdk --hidden-import win32timezone `
            single_launcher.py
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败。' }
    }
    finally { Pop-Location }

    Write-Step '运行 EXE 自检（检查内置配置、素材和入口，不启动机器人或网页服务）'
    & $ExePath --self-test
    if ($LASTEXITCODE -ne 0) { throw 'EXE 自检未通过。' }

    Write-Host "`n打包完成：$ExePath" -ForegroundColor Green
    Write-Host '发布时只需提供 WeChatBot.exe；首次运行会在同目录生成可编辑配置和素材。'
}
catch {
    Write-Host "`n打包失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
