<#
================================================================================
 기출 문항집 작업기 (gichul-workbench) — 갱신
================================================================================

   irm https://raw.githubusercontent.com/rochelobeJYJ/gichul-workbench/main/update.ps1 | iex

 왜 install.ps1 과 따로 있는가:
   install.ps1 은 몇 번을 실행해도 안전합니다(있으면 갱신). 그러니 기능상으로는
   이 파일이 없어도 됩니다. 그런데 '최신으로만 맞추고 싶은' 분에게 '설치' 라고
   적힌 것을 실행하라고 하면 덮어써질까 봐 손이 멈춥니다. 그래서 이름만 따로 두고
   내용은 install.ps1 을 그대로 부릅니다 — 두 벌로 갈라져 서로 달라지는 일이 없게.

 이 파일이 스스로 하는 일은 '이미 설치된 자리를 찾는 것' 하나뿐입니다.
 못 찾으면 아무것도 하지 않고 설치 방법을 알려 드립니다.

 ★ 이 파일을 저장해서 직접 실행하지 마십시오. 위의 한 줄로 실행하는 용도입니다.
   PowerShell 5.1 은 BOM 없는 .ps1 을 cp949 로 읽어서 한글이 깨집니다. 그렇다고
   BOM 을 붙이면 이번엔 `irm | iex` 가 깨집니다(둘 다 실측). 한 줄 실행이 본체라
   BOM 을 붙이지 않았습니다.
   더블클릭으로 갱신하고 싶으시면 설치 폴더 안의 update.cmd 를 쓰십시오.
   그 파일은 설치기가 만들어 두며, 인코딩·실행정책에 걸리지 않습니다.
================================================================================
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

# 안내문이 깨지면 안내가 아닙니다. 콘솔이 한글을 못 내보내면 UTF-8 로 바꿉니다.
try {
    $enc = [Console]::OutputEncoding
    if ($enc.GetString($enc.GetBytes('한글')) -ne '한글') {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    }
} catch { }

try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

$InstallUrl = 'https://raw.githubusercontent.com/rochelobeJYJ/gichul-workbench/main/install.ps1'

Write-Host ""
Write-Host "── 기출 문항집 작업기 — 설치된 자리를 찾습니다" -ForegroundColor Cyan

# install.ps1 이 쓰는 자리와 같은 순서로 봅니다. scripts\gw.py 가 있으면 그 폴더가 맞습니다.
$found = $null
$where = @(
    $env:GW_INSTALL_DIR,
    (Join-Path $HOME '.claude\skills\gichul-workbench'),
    (Join-Path $HOME '.codex\skills\gichul-workbench'),
    (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'gichul-workbench'),
    (Join-Path $HOME 'Documents\gichul-workbench')
)
# 문서 폴더가 OneDrive 로 옮겨져 있지 않으면 위 두 줄이 같은 자리가 됩니다.
# 같은 경로를 두 번 보여 주면 '왜 두 번 찾았지' 하고 멈칫하게 됩니다.
$where = $where | Where-Object { $_ } | Select-Object -Unique
foreach ($w in $where) {
    if ($w -and (Test-Path (Join-Path $w 'scripts\gw.py'))) { $found = $w; break }
}

if (-not $found) {
    Write-Host ""
    Write-Host "  아직 설치되어 있지 않습니다." -ForegroundColor Yellow
    Write-Host "  찾아본 자리:"
    foreach ($w in $where) { if ($w) { Write-Host "    $w" } }
    Write-Host ""
    Write-Host "  먼저 설치부터 하십시오. 아래 한 줄을 붙여넣으시면 됩니다."
    Write-Host ""
    Write-Host "    irm $InstallUrl | iex"
    Write-Host ""
    Write-Host "  다른 자리에 설치해 두셨다면 그 경로를 먼저 알려 주십시오."
    Write-Host '    $env:GW_INSTALL_DIR="설치한\경로"'
    Write-Host ""
    exit 1
}

Write-Host "   $found" -ForegroundColor Green
Write-Host "   여기를 최신으로 맞춥니다. 작업 폴더(workspace)는 건드리지 않습니다."

# 찾은 자리를 install.ps1 에 넘기고, 나머지는 전부 install.ps1 이 합니다.
$env:GW_INSTALL_DIR = $found
try {
    $script = Invoke-RestMethod -Uri $InstallUrl
} catch {
    Write-Host ""
    Write-Host "  설치 스크립트를 받지 못했습니다." -ForegroundColor Red
    Write-Host "  이유: $($_.Exception.Message)"
    Write-Host "  인터넷 연결을 확인해 주십시오. 학교망에서 github.com 이 막혀 있을 수 있습니다."
    Write-Host ""
    exit 1
}
Invoke-Expression $script
