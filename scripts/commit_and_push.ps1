<#
.SYNOPSIS
    변경사항을 확인 -> 테스트 실행 -> 커밋 -> (선택) push까지 한 번에 진행하는 스크립트.

.DESCRIPTION
    Windows PowerShell(또는 VS Code 통합 터미널)에서 실행합니다. 매 단계마다 사용자에게
    확인을 받으므로, 뭔가 잘못됐다 싶으면 아무 때나 'n'을 입력해 중단할 수 있습니다.

    1) 현재 변경사항(git status)을 보여주고 커밋 진행 여부를 묻습니다.
    2) pytest를 실행합니다 - 실패하면 여기서 중단합니다(깨진 코드를 커밋하지 않기 위함).
    3) 커밋 메시지를 입력받아 커밋합니다.
    4) push 여부를 물어보고, 원한다면 현재 브랜치로 push합니다.

.PARAMETER Message
    커밋 메시지를 미리 지정하고 싶으면 사용합니다(생략하면 실행 중에 물어봅니다).
    예: .\scripts\commit_and_push.ps1 -Message "레드닷 검출 임계값 조정"

.EXAMPLE
    PS> .\scripts\commit_and_push.ps1

.NOTES
    최초 1회 아래 명령으로 실행 권한을 허용해야 할 수 있습니다(관리자 권한 불필요,
    현재 사용자 범위로만 허용):
        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
    또는 매번 아래처럼 정책을 우회해서 실행해도 됩니다:
        powershell -ExecutionPolicy Bypass -File scripts\commit_and_push.ps1
#>

param(
    [string]$Message
)

$ErrorActionPreference = "Stop"

function Write-Section($text) {
    Write-Host ""
    Write-Host "== $text ==" -ForegroundColor Cyan
}

# 저장소 최상위 폴더 기준으로 동작하도록 이동 (스크립트를 어디서 실행하든 안전하게)
$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) {
    Write-Host "git 저장소 안에서 실행해야 합니다." -ForegroundColor Red
    exit 1
}
Set-Location $repoRoot

Write-Section "현재 변경사항 (git status)"
git status

$hasChanges = -not [string]::IsNullOrWhiteSpace((git status --porcelain))
if (-not $hasChanges) {
    Write-Host "커밋할 변경사항이 없습니다." -ForegroundColor Yellow
    exit 0
}

$confirm = Read-Host "`n위 변경사항을 커밋 진행할까요? (y/n)"
if ($confirm -ne "y") {
    Write-Host "취소되었습니다."
    exit 0
}

Write-Section "테스트 실행 (pytest)"
python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n테스트가 실패했습니다 - 커밋을 중단합니다. 위 오류를 먼저 해결한 뒤 다시 실행하세요." -ForegroundColor Red
    exit 1
}
Write-Host "테스트 통과." -ForegroundColor Green

if (-not $Message) {
    $Message = Read-Host "`n커밋 메시지를 입력하세요"
}
if ([string]::IsNullOrWhiteSpace($Message)) {
    Write-Host "커밋 메시지가 비어있어 취소합니다." -ForegroundColor Red
    exit 1
}

Write-Section "변경사항 스테이징 및 커밋"
git add -A
git status --short  # 무엇이 스테이징됐는지 마지막으로 한 번 더 보여줌 (민감 파일 실수 포함 방지)

$stageConfirm = Read-Host "`n위 파일들을 이 메시지로 커밋할까요? (y/n)"
if ($stageConfirm -ne "y") {
    git reset
    Write-Host "취소되었습니다(스테이징 해제)."
    exit 0
}

git commit -m $Message

Write-Section "push"
$branch = git rev-parse --abbrev-ref HEAD
$pushConfirm = Read-Host "원격 저장소(origin/$branch)로 push 할까요? (y/n)"
if ($pushConfirm -eq "y") {
    git push origin $branch
    Write-Host "push 완료." -ForegroundColor Green
} else {
    Write-Host "로컬에만 커밋되었습니다. 나중에 다음 명령으로 올리세요: git push origin $branch" -ForegroundColor Yellow
}
