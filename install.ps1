<#
================================================================================
 기출 문항집 작업기 (gichul-workbench) — 설치기
================================================================================

 이 스크립트가 하는 일은 이것이 전부입니다. 그 밖의 일은 하지 않습니다.

   1. uv(파이썬 실행기) 가 없으면 받습니다.  https://astral.sh/uv/install.ps1
   2. 도구를 내려받습니다.                    https://github.com/rochelobeJYJ/gichul-workbench
      git 이 있으면 git 으로, 없으면 zip 으로 받습니다.
   3. 도구 폴더 안에 .venv 를 만들고 requirements.txt 의 패키지를 넣습니다.
   4. 도구 폴더에 gw.cmd(실행) 와 update.cmd(갱신) 두 파일을 만듭니다.
   5. 잘 됐는지 확인하고(scripts/bootstrap.py) 다음에 할 일을 알려 드립니다.

 실행 정책(ExecutionPolicy)에 막히면 — 위 한 줄이 서명 오류로 거절당하는 학교
 컴퓨터가 있습니다. 그때는 설정을 바꾸지 마시고 이 형태로 한 번만 실행하십시오.
 이 실행에만 적용되고 컴퓨터 설정은 그대로입니다.

   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/rochelobeJYJ/gichul-workbench/main/install.ps1 | iex"

 하지 않는 일:
   - 관리자 권한을 요구하지 않습니다.  전부 사용자 폴더 안에서만 일어납니다.
   - 시스템 파이썬을 건드리지 않습니다.  패키지는 .venv 안에만 들어갑니다.
   - 이미 설치돼 있으면 지우지 않고 갱신만 합니다.
     특히 작업 폴더(workspace/)는 어떤 경우에도 건드리지 않습니다.
   - 아무것도 바깥으로 보내지 않습니다.

 쓰는 법 — PowerShell 창에 이 한 줄:

   irm https://raw.githubusercontent.com/rochelobeJYJ/gichul-workbench/main/install.ps1 | iex

 설치 위치를 직접 정하고 싶으시면 앞에 한 줄을 붙이십시오. 예를 들어 Codex 쪽으로:

   $env:GW_INSTALL_DIR="$HOME\.codex\skills\gichul-workbench"; irm https://raw.githubusercontent.com/rochelobeJYJ/gichul-workbench/main/install.ps1 | iex

 다시 실행하면 갱신이 됩니다. update.ps1 은 이 스크립트를 부르는 얇은 껍데기입니다.
================================================================================
#>

param(
    # 설치 위치. 비워 두면 아래 Resolve-InstallDir 이 알아서 고릅니다.
    # irm | iex 로 실행할 때는 인자를 줄 수 없으므로 환경변수로도 받습니다.
    [string]$Dir = $env:GW_INSTALL_DIR
)

$ErrorActionPreference = 'Stop'

# PowerShell 5.1 의 진행률 막대는 파일 하나 받는 데 몇 배로 느리게 만듭니다.
$ProgressPreference = 'SilentlyContinue'

$RepoUrl = 'https://github.com/rochelobeJYJ/gichul-workbench.git'
$ZipUrl  = 'https://github.com/rochelobeJYJ/gichul-workbench/archive/refs/heads/main.zip'


# ─────────────────────────────────────────────────────────────────────────────
# 0. 화면에 한글이 나오게 한다
#    영어 오류 한 줄에서 멈추는 분들을 위한 도구입니다. 안내문이 ???? 로 보이면
#    설치가 성공해도 실패한 것과 같습니다. 콘솔이 한글을 못 내보내면 UTF-8 로 바꿉니다.
# ─────────────────────────────────────────────────────────────────────────────
try {
    $enc = [Console]::OutputEncoding
    if ($enc.GetString($enc.GetBytes('한글')) -ne '한글') {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    }
} catch {
    # 콘솔이 아닌 곳(파이프 등)에서 실행되면 여기서 막힐 수 있습니다. 치명적이지 않습니다.
}

# PowerShell 5.1 은 기본이 TLS 1.0 인 환경이 아직 있습니다. 그러면 github 접속이 조용히 실패합니다.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }


# ─────────────────────────────────────────────────────────────────────────────
# 1. 말하기 도우미
#    실패는 반드시 '무엇을 하다가 / 왜 / 그래서 무엇을 하면 되는지' 세 줄로 말합니다.
#    '알 수 없는 오류' 로 끝내지 않습니다.
# ─────────────────────────────────────────────────────────────────────────────
function Write-Step ([string]$Text) { Write-Host ""; Write-Host "── $Text" -ForegroundColor Cyan }
function Write-Ok   ([string]$Text) { Write-Host "   $Text" -ForegroundColor Green }
function Write-Info ([string]$Text) { Write-Host "   $Text" }
function Write-Warn ([string]$Text) { Write-Host "   [주의] $Text" -ForegroundColor Yellow }

function Stop-Install {
    param([string]$What, [string]$Why, [string[]]$How)
    Write-Host ""
    Write-Host "────────────────────────────────────────────────────" -ForegroundColor Red
    Write-Host " 설치를 멈췄습니다." -ForegroundColor Red
    Write-Host "────────────────────────────────────────────────────" -ForegroundColor Red
    Write-Host " 하던 일 : $What"
    Write-Host " 이유    : $Why"
    if ($How) {
        Write-Host " 이렇게 해 보십시오 :"
        foreach ($line in $How) { Write-Host "   $line" }
    }
    Write-Host ""
    Write-Host " 그래도 안 되면 이 화면을 그대로 복사해서 알려 주십시오."
    Write-Host " https://github.com/rochelobeJYJ/gichul-workbench/issues"
    Write-Host ""
    exit 1
}

# 외부 프로그램(uv, git)의 실패는 예외가 아니라 종료코드로 옵니다. 반드시 이걸로 확인합니다.
#
# 두 가지를 조심해야 합니다.
#  - 매개변수 이름을 $Args 로 지으면 안 됩니다. PowerShell 이 이미 쓰는 이름이라
#    조용히 비어서 넘어가고, uv 가 아무 명령도 못 받아 도움말만 띄웁니다(실제로 겪음).
#  - Out-Host 로 흘려보내지 않으면 프로그램의 출력이 이 함수의 반환값에 섞여 들어와
#    종료코드가 배열이 됩니다.
#  - uv 도 git 도 진행 상황을 stderr 로 씁니다. 그 자체는 오류가 아닙니다.
#    그런데 이 창의 출력이 어딘가로 넘겨지는 상태면 PowerShell 이 그 줄들을 오류로
#    바꿔 버리고, ErrorActionPreference=Stop 때문에 멀쩡한 설치가 중단됩니다.
#    그래서 외부 프로그램을 부르는 동안만 그 규칙을 풀어 둡니다. 판정은 종료코드로 합니다.
function Invoke-Native {
    param([string]$Exe, [string[]]$ArgList)
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @ArgList | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $saved
    }
}


Write-Host ""
Write-Host "════════════════════════════════════════════════════" -ForegroundColor White
Write-Host "  기출 문항집 작업기 설치" -ForegroundColor White
Write-Host "════════════════════════════════════════════════════" -ForegroundColor White
Write-Info "수능·모의평가 기출을 받아 문항별로 자르고 단원별 학습지를 만드는 도구입니다."


# ─────────────────────────────────────────────────────────────────────────────
# 2. uv 를 확보한다
#    uv 를 쓰는 이유: 파이썬이 아예 없는 컴퓨터에도 파이썬을 관리자 권한 없이 넣어 줍니다.
#    '파이썬을 먼저 설치하고 PATH 에 체크하세요' 가 바로 사람들이 포기하는 지점입니다.
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "1/5  uv 확인"

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # 설치 직후에는 PATH 가 이 창에 아직 반영되지 않습니다. 알려진 자리를 직접 봅니다.
    $known = @(
        (Join-Path $HOME '.local\bin\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'uv\bin\uv.exe')
    )
    foreach ($p in $known) { if ($p -and (Test-Path $p)) { return $p } }
    return $null
}

$Uv = Find-Uv
if ($Uv) {
    Write-Ok "이미 있습니다 — $Uv"
} else {
    Write-Info "uv 가 없어서 받습니다. 사용자 폴더에만 설치되고 관리자 권한은 필요 없습니다."
    try {
        # 공식 설치 스크립트. 자식 PowerShell 로 돌리는 이유는 실행 정책(ExecutionPolicy)이
        # 걸려 있는 학교 컴퓨터에서도 이 한 번은 통과시키기 위해서입니다.
        Invoke-Native -Exe 'powershell' -ArgList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-Command', 'irm https://astral.sh/uv/install.ps1 | iex') | Out-Null
    } catch {
        Stop-Install -What 'uv 내려받기' -Why $_.Exception.Message -How @(
            '인터넷 연결을 확인해 주십시오.',
            '학교망이라면 https://astral.sh 이 막혀 있을 수 있습니다. 다른 망에서 시도해 보십시오.'
        )
    }
    $Uv = Find-Uv
    if (-not $Uv) {
        Stop-Install -What 'uv 설치 확인' -Why 'uv 설치 스크립트는 끝났는데 uv.exe 가 보이지 않습니다.' -How @(
            'PowerShell 창을 닫았다 새로 열고 이 설치를 다시 실행해 보십시오.',
            '백신이 설치를 막았을 수 있습니다. 차단 기록을 확인해 주십시오.'
        )
    }
    # 이 창에서 바로 쓸 수 있게 PATH 에 넣습니다. 이 창에만 적용되고 시스템은 건드리지 않습니다.
    $env:Path = (Split-Path $Uv) + ';' + $env:Path
    Write-Ok "설치했습니다 — $Uv"
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. 설치 위치를 정한다
#    ~/.claude/skills/ 에 두면 Claude Code 가 이 도구를 알아서 인식합니다.
#    Codex 만 쓰신다면 ~/.codex/skills/ 입니다. 둘 다 없으면 문서 폴더로 갑니다.
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "2/5  설치 위치"

function Resolve-InstallDir {
    param([string]$Given)

    if ($Given) {
        return [pscustomobject]@{ Path = $Given; Why = '직접 지정하신 위치입니다.' }
    }

    # 이미 설치된 곳이 있으면 거기를 씁니다. 두 벌이 생기면 어느 쪽을 고쳤는지 모르게 됩니다.
    $candidates = @(
        (Join-Path $HOME '.claude\skills\gichul-workbench'),
        (Join-Path $HOME '.codex\skills\gichul-workbench')
    )
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c 'scripts\gw.py')) {
            return [pscustomobject]@{ Path = $c; Why = '이미 설치돼 있어서 그 자리를 갱신합니다.' }
        }
    }

    if (Test-Path (Join-Path $HOME '.claude')) {
        return [pscustomobject]@{
            Path = (Join-Path $HOME '.claude\skills\gichul-workbench')
            Why  = 'Claude Code 를 쓰고 계셔서, 자동으로 인식되는 자리에 넣습니다.' }
    }
    if (Test-Path (Join-Path $HOME '.codex')) {
        return [pscustomobject]@{
            Path = (Join-Path $HOME '.codex\skills\gichul-workbench')
            Why  = 'Codex 를 쓰고 계셔서, 자동으로 인식되는 자리에 넣습니다.' }
    }

    # 문서 폴더는 OneDrive 로 옮겨져 있는 경우가 많아 레지스트리에 적힌 실제 자리를 봅니다.
    $docs = $null
    try { $docs = [Environment]::GetFolderPath('MyDocuments') } catch { }
    if (-not $docs) { $docs = Join-Path $HOME 'Documents' }
    return [pscustomobject]@{
        Path = (Join-Path $docs 'gichul-workbench')
        Why  = 'Claude Code 도 Codex 도 안 보여서 문서 폴더에 넣습니다.' }
}

$choice = Resolve-InstallDir -Given $Dir
# update.cmd 는 %~dp0 를 넘겨서 경로 끝에 \ 가 붙습니다. 그대로 두면 화면 안내가 지저분해집니다.
$Dir    = $choice.Path.TrimEnd('\', '/')
Write-Ok "$Dir"
Write-Info $choice.Why
if (-not $env:GW_INSTALL_DIR -and -not (Test-Path (Join-Path $Dir 'scripts\gw.py'))) {
    Write-Info "다른 자리에 넣고 싶으시면 이 설치를 멈추고(Ctrl+C) 아래처럼 위치를 먼저 정하십시오."
    Write-Info '  $env:GW_INSTALL_DIR="원하는\경로"'
}

try {
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
} catch {
    Stop-Install -What '설치 폴더 만들기' -Why $_.Exception.Message -How @(
        '그 폴더에 쓸 권한이 없는 것 같습니다.',
        '아래처럼 다른 위치를 정한 뒤 다시 실행해 보십시오.',
        '  $env:GW_INSTALL_DIR="$HOME\Documents\gichul-workbench"'
    )
}


# ─────────────────────────────────────────────────────────────────────────────
# 4. 도구를 내려받는다 (git 또는 zip)
#    학교 컴퓨터는 git 설치가 막혀 있는 경우가 실제로 많습니다. zip 길이 반드시 있어야 합니다.
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "3/5  도구 내려받기"

$Git      = (Get-Command git -ErrorAction SilentlyContinue)
$isGitDir = Test-Path (Join-Path $Dir '.git')
$isThere  = Test-Path (Join-Path $Dir 'scripts\gw.py')

function Get-ByZip {
    param([string]$Target)

    $tmp = Join-Path $env:TEMP ('gichul-' + [guid]::NewGuid().ToString('N'))
    $zip = "$tmp.zip"
    try {
        Write-Info "zip 으로 받습니다 (약 6MB)..."
        Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
        Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force

        # 압축을 풀면 gichul-workbench-main/ 한 겹이 더 생깁니다. 그 안쪽을 덮어씁니다.
        $inner = Get-ChildItem -LiteralPath $tmp -Directory | Select-Object -First 1
        if (-not $inner) { throw 'zip 안이 비어 있습니다.' }

        # 지우고 새로 넣지 않고 '덮어쓰기' 만 합니다.
        # 선생님 작업 폴더(workspace/)와 직접 만드신 파일을 지우지 않기 위해서입니다.
        Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $Target -Recurse -Force
    } finally {
        Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($Git -and $isGitDir) {
    Write-Info "이미 있습니다. 새 내용만 받아 옵니다."
    # --ff-only: 선생님이 고친 파일을 자동으로 합치려 들지 않습니다. 충돌이 나면 그냥 멈춥니다.
    $code = Invoke-Native -Exe $Git.Source -ArgList @('-C', $Dir, 'pull', '--ff-only')
    if ($code -ne 0) {
        # 갱신 실패는 치명적이지 않습니다. 기존 설치는 그대로 멀쩡하니 계속 진행합니다.
        Write-Warn "새 내용을 받지 못했습니다. 기존 설치는 그대로 쓰실 수 있습니다."
        Write-Info "저장소 안의 파일을 직접 고치셨거나 인터넷이 안 되면 이렇게 됩니다."
        Write-Info "고치신 내용을 지키고 싶으시면 그대로 두시고, 최신으로 맞추고 싶으시면"
        Write-Info "폴더 이름을 바꿔 두고(예: gichul-workbench-old) 다시 설치하십시오."
    } else {
        Write-Ok "최신으로 맞췄습니다."
    }
} elseif ($Git -and -not $isThere) {
    Write-Info "git 으로 받습니다..."
    $code = Invoke-Native -Exe $Git.Source -ArgList @('clone', '--depth', '1', $RepoUrl, $Dir)
    if ($code -ne 0) {
        Write-Warn "git 으로 받지 못했습니다. zip 으로 다시 시도합니다."
        try { Get-ByZip -Target $Dir } catch {
            Stop-Install -What '도구 내려받기' -Why $_.Exception.Message -How @(
                '인터넷 연결을 확인해 주십시오.',
                '학교망에서 github.com 이 막혀 있을 수 있습니다.'
            )
        }
        Write-Ok "받았습니다 (zip)."
    } else {
        Write-Ok "받았습니다 (git)."
    }
} else {
    # git 이 없거나, git 없이 zip 으로 깔아 둔 폴더를 갱신하는 경우입니다.
    if (-not $Git) { Write-Info "git 이 없어서 zip 으로 받습니다. 문제 없습니다." }
    try { Get-ByZip -Target $Dir } catch {
        Stop-Install -What '도구 내려받기(zip)' -Why $_.Exception.Message -How @(
            '인터넷 연결을 확인해 주십시오.',
            "학교망에서 github.com 이 막혀 있을 수 있습니다.",
            "직접 받으셔도 됩니다: $ZipUrl",
            "받은 zip 을 풀어서 안쪽 폴더 내용을 $Dir 에 넣으시면 같습니다."
        )
    }
    Write-Ok "받았습니다 (zip)."
}

if (-not (Test-Path (Join-Path $Dir 'scripts\gw.py'))) {
    Stop-Install -What '내려받은 내용 확인' -Why "$Dir 안에 scripts\gw.py 가 없습니다." -How @(
        '내려받기가 중간에 끊긴 것 같습니다. 이 설치를 한 번 더 실행해 보십시오.'
    )
}


# ─────────────────────────────────────────────────────────────────────────────
# 5. 파이썬 환경과 패키지
#    도구 폴더 안 .venv 에만 넣습니다. 시스템 파이썬은 그대로 둡니다.
#    (.venv 는 저장소의 .gitignore 에 이미 들어 있어서 갱신에 방해되지 않습니다.)
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "4/5  파이썬 준비"

$Venv   = Join-Path $Dir '.venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$Req    = Join-Path $Dir 'requirements.txt'

function New-Venv {
    param([string]$PinnedVersion)

    Remove-Item -LiteralPath $Venv -Recurse -Force -ErrorAction SilentlyContinue
    if ($PinnedVersion) {
        # 파이썬이 너무 최신이면 PyMuPDF 같은 패키지의 준비된 설치본이 아직 없습니다.
        # 그때 안전한 버전을 uv 가 직접 받아 씁니다. 시스템에는 영향이 없습니다.
        return (Invoke-Native -Exe $Uv -ArgList @('venv', $Venv, '--python', $PinnedVersion))
    }
    return (Invoke-Native -Exe $Uv -ArgList @('venv', $Venv))
}

if (Test-Path $VenvPy) {
    Write-Info "파이썬 환경이 이미 있습니다. 그대로 씁니다."
} else {
    $code = New-Venv
    if ($code -ne 0) {
        Write-Info "쓸 수 있는 파이썬이 없어서 uv 가 직접 받아 옵니다 (약 25MB)..."
        $code = New-Venv -PinnedVersion '3.12'
    }
    if ($code -ne 0 -or -not (Test-Path $VenvPy)) {
        Stop-Install -What '파이썬 환경 만들기' -Why 'uv 가 파이썬 환경을 만들지 못했습니다. 위에 뜬 영어 메시지가 원인입니다.' -How @(
            '백신이 파이썬 실행 파일을 막았을 수 있습니다.',
            '아래처럼 다른 위치에 설치해 보십시오. 회사·학교 정책이 특정 폴더만 막는 경우가 있습니다.',
            '  $env:GW_INSTALL_DIR="$HOME\Documents\gichul-workbench"'
        )
    }
    Write-Ok "파이썬 환경을 만들었습니다."
}

# ★ 반드시 설치 폴더 안에서 실행합니다.
#   requirements.txt 안에 `-e .`(이 폴더 자신) 이 들어 있고, 그 `.` 은 '파일이 있는 곳'
#   이 아니라 '지금 서 있는 곳' 을 가리킵니다. 선생님이 어느 폴더에서 설치를 시작하셨는지는
#   알 수 없으므로, 자리를 옮겨 두지 않으면 엉뚱한 폴더를 설치하려 듭니다.
function Install-Requirements {
    Push-Location $Dir
    try {
        return (Invoke-Native -Exe $Uv -ArgList @('pip', 'install', '--python', $VenvPy, '-r', $Req))
    } finally {
        Pop-Location
    }
}

Write-Info "필요한 패키지를 받습니다 (PDF 처리·이미지 등)..."
$code = Install-Requirements
if ($code -ne 0) {
    # 대개는 파이썬 버전이 너무 최신이라 준비된 설치본이 없는 경우입니다. 안전한 버전으로 한 번 더.
    Write-Warn "패키지 설치가 실패했습니다. 파이썬 3.12 로 다시 만들어 봅니다."
    $code = New-Venv -PinnedVersion '3.12'
    if ($code -eq 0) { $code = Install-Requirements }
    if ($code -ne 0) {
        Stop-Install -What '패키지 설치' -Why '패키지를 받아 넣지 못했습니다. 위에 뜬 영어 메시지가 원인입니다.' -How @(
            '학교망이 pypi.org 를 막고 있을 수 있습니다. 다른 망에서 시도해 보십시오.',
            '백신이 설치를 막았을 수도 있습니다.'
        )
    }
}
Write-Ok "패키지를 넣었습니다."


# ─────────────────────────────────────────────────────────────────────────────
# 6. 실행기(gw.cmd) 를 만든다
#    .ps1 이 아니라 .cmd 인 이유: PowerShell 실행 정책(ExecutionPolicy)이 막혀 있어도
#    .cmd 는 그냥 돌아갑니다. 여기서 또 막히면 안 됩니다.
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "5/5  실행기 만들기"

# chcp 65001 을 쓰는 이유: 배치 파일 안의 한글은 콘솔 코드페이지가 안 맞으면 깨집니다.
# 오류를 알려야 하는 자리에서 깨지면 아무 소용이 없으므로 그 갈래에서만 UTF-8 로 바꿉니다.
$launcher = @'
@echo off
rem ---------------------------------------------------------------------------
rem  기출 문항집 작업기 실행기.  설치기(install.ps1)가 만들었습니다.
rem  쓰는 법:   .\gw subjects        .\gw build --subject earth-science-ii
rem  하는 일은 이 폴더의 .venv 파이썬으로 scripts\gw.py 를 부르는 것뿐입니다.
rem ---------------------------------------------------------------------------
setlocal
set "GW_HOME=%~dp0"
if not exist "%GW_HOME%.venv\Scripts\python.exe" goto :nopy
"%GW_HOME%.venv\Scripts\python.exe" "%GW_HOME%scripts\gw.py" %*
exit /b %ERRORLEVEL%

:nopy
chcp 65001 >nul
echo.
echo  파이썬 환경이 없습니다. 설치가 끝나지 않았거나 .venv 폴더가 지워졌습니다.
echo  PowerShell 창에 아래 한 줄을 붙여넣어 다시 설치하시면 됩니다.
echo.
echo    irm https://raw.githubusercontent.com/rochelobeJYJ/gichul-workbench/main/install.ps1 ^| iex
echo.
exit /b 1
'@

# UTF-8(BOM 없음)으로 씁니다. Set-Content 의 기본값은 ANSI 라 한글이 깨집니다.
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $Dir 'gw.cmd'), ($launcher -replace "`r?`n", "`r`n"), $utf8)

# `uv run gw subjects` 도 되게 합니다. uv run 은 이 폴더의 .venv 를 찾아
# 그 Scripts 안에서 명령을 찾습니다.
#
# 저장소가 pyproject.toml 로 `gw` 명령을 직접 제공하면 위 패키지 설치 때
# .venv\Scripts\gw.exe 가 이미 생깁니다. 그때는 아무것도 하지 않습니다 —
# 우리 사본을 덧대면 어느 쪽이 도는지 사람이 알 수 없게 됩니다.
# 판정은 gw.exe 하나로만 합니다. gw.cmd 는 우리가 지난번에 만들어 둔 것일 수 있어서,
# 그것을 보고 '저장소가 준다' 고 판단하면 스스로를 보고 속는 셈이 됩니다.
$realGw = Join-Path $Venv 'Scripts\gw.exe'
$ourShim = Join-Path $Venv 'Scripts\gw.cmd'
if (Test-Path $realGw) {
    Write-Info "gw 명령은 저장소가 직접 제공합니다. 그대로 씁니다."
    # 옛 설치본에서 올라온 경우 우리 사본이 남아 있습니다. 둘이 나란히 있으면
    # 어느 쪽이 도는지 사람이 알 수 없으니 치웁니다.
    if (Test-Path $ourShim) { Remove-Item $ourShim -Force -ErrorAction SilentlyContinue }
} else {
    # 옛 저장소(패키지 설정이 없던 시절)에서도 `uv run gw` 가 되도록 이어 줍니다.
    $shim = @'
@echo off
rem  uv run gw ... 를 위한 연결 고리. 위 폴더의 gw.cmd 와 같은 일을 합니다.
"%~dp0python.exe" "%~dp0..\..\scripts\gw.py" %*
'@
    [System.IO.File]::WriteAllText($ourShim, ($shim -replace "`r?`n", "`r`n"), $utf8)
}

# 더블클릭으로 갱신할 수 있는 파일도 같이 둡니다.
# .ps1 이 아니라 .cmd 인 이유가 둘 있습니다.
#  - 실행 정책에 걸리지 않습니다.
#  - .ps1 파일을 직접 실행하면 PowerShell 5.1 이 cp949 로 읽어 한글이 깨지고,
#    깨진 따옴표 때문에 파일이 아예 해석되지 않습니다(실측). .cmd 는 그 문제가 없습니다.
$updater = @'
@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------------------
rem  기출 문항집 작업기 — 최신으로 맞추기.  더블클릭하시면 됩니다.
rem  하는 일은 설치 한 줄을 이 폴더를 향해 다시 실행하는 것뿐입니다.
rem  작업 폴더(workspace)는 건드리지 않습니다.
rem ---------------------------------------------------------------------------
rem  경로는 환경변수로 넘깁니다. 명령줄에 끼워 넣으면 경로에 따옴표나 공백이
rem  들어 있을 때 깨집니다.
set "GW_INSTALL_DIR=%~dp0"
echo.
echo  최신으로 맞춥니다:  %GW_INSTALL_DIR%
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/rochelobeJYJ/gichul-workbench/main/install.ps1 | iex"
echo.
echo  이 창은 아무 키나 누르시면 닫힙니다.
pause >nul
'@
[System.IO.File]::WriteAllText((Join-Path $Dir 'update.cmd'), ($updater -replace "`r?`n", "`r`n"), $utf8)

Write-Ok "gw.cmd (실행) 와 update.cmd (갱신) 를 만들었습니다."


# ─────────────────────────────────────────────────────────────────────────────
# 7. 진짜로 도는지 확인한다
#    저장소가 원래 갖고 있는 점검 스크립트를 그대로 씁니다. 점검 기준을 두 벌 만들지 않습니다.
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "── 확인" -ForegroundColor Cyan
$bootCode = Invoke-Native -Exe $VenvPy -ArgList @((Join-Path $Dir 'scripts\bootstrap.py'))

if ($bootCode -ne 0) {
    Stop-Install -What '설치 확인' -Why '위 점검에서 빠진 것이 나왔습니다.' -How @(
        '이 설치를 한 번 더 실행해 보십시오. 중간에 끊긴 경우가 대부분입니다.'
    )
}


# ─────────────────────────────────────────────────────────────────────────────
# 8. 다음에 무엇을 하면 되는지
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  설치가 끝났습니다." -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  설치 위치 : $Dir"
Write-Host ""
Write-Host "  ┌ Claude Code 나 Codex 를 쓰고 계시다면 ─────────────" -ForegroundColor White
Write-Host "  │  아무것도 외우실 필요 없습니다. 그냥 말씀하시면 됩니다."
Write-Host "  │"
Write-Host "  │    지구과학2 기출로 단원별 학습지 만들어줘"
Write-Host "  │"
Write-Host "  │  도구를 못 찾는다고 하면 이 경로를 알려 주십시오."
Write-Host "  │    $Dir"
Write-Host "  └────────────────────────────────────────────────────"
Write-Host ""
Write-Host "  ┌ 직접 하시겠다면 ───────────────────────────────────" -ForegroundColor White
Write-Host "  │  아래 두 줄을 PowerShell 에 붙여넣어 보십시오."
Write-Host "  │"
Write-Host "  │    cd `"$Dir`""
Write-Host "  │    .\gw subjects"
Write-Host "  │"
Write-Host "  │  과목 목록이 나오면 성공입니다."
Write-Host "  │  그다음 순서는 README.md 의 '처음 써보기' 에 있습니다."
Write-Host "  └────────────────────────────────────────────────────"
Write-Host ""
Write-Host "  ┌ 나중에 최신으로 맞추실 때 ─────────────────────────" -ForegroundColor White
Write-Host "  │  설치 폴더의 update.cmd 를 더블클릭하시면 됩니다."
Write-Host "  │  맨 처음 그 한 줄을 다시 실행하셔도 똑같습니다."
Write-Host "  │  작업하신 내용(workspace 폴더)은 어느 쪽이든 그대로 남습니다."
Write-Host "  └────────────────────────────────────────────────────"
Write-Host ""
