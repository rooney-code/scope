#!/usr/bin/env bash
# 변경사항을 확인 -> 테스트 실행 -> 커밋 -> (선택) push까지 한 번에 진행하는 스크립트.
# (commit_and_push.ps1과 동일한 흐름의 bash 버전 - WSL/Linux/Mac에서 작업할 때 사용)
#
# 사용법: ./scripts/commit_and_push.sh ["커밋 메시지"]
#   커밋 메시지를 인자로 주지 않으면 실행 중에 물어봅니다.
#
# 최초 1회: chmod +x scripts/commit_and_push.sh

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "git 저장소 안에서 실행해야 합니다." >&2
    exit 1
}
cd "$repo_root"

echo
echo "== 현재 변경사항 (git status) =="
git status

if [ -z "$(git status --porcelain)" ]; then
    echo "커밋할 변경사항이 없습니다."
    exit 0
fi

read -r -p $'\n위 변경사항을 커밋 진행할까요? (y/n) ' confirm
if [ "$confirm" != "y" ]; then
    echo "취소되었습니다."
    exit 0
fi

echo
echo "== 테스트 실행 (pytest) =="
if ! python3 -m pytest -q; then
    echo -e "\n테스트가 실패했습니다 - 커밋을 중단합니다. 위 오류를 먼저 해결한 뒤 다시 실행하세요." >&2
    exit 1
fi
echo "테스트 통과."

message="${1:-}"
if [ -z "$message" ]; then
    read -r -p $'\n커밋 메시지를 입력하세요: ' message
fi
if [ -z "$message" ]; then
    echo "커밋 메시지가 비어있어 취소합니다." >&2
    exit 1
fi

echo
echo "== 변경사항 스테이징 및 커밋 =="
git add -A
git status --short  # 무엇이 스테이징됐는지 마지막으로 한 번 더 보여줌 (민감 파일 실수 포함 방지)

read -r -p $'\n위 파일들을 이 메시지로 커밋할까요? (y/n) ' stage_confirm
if [ "$stage_confirm" != "y" ]; then
    git reset
    echo "취소되었습니다(스테이징 해제)."
    exit 0
fi

git commit -m "$message"

echo
echo "== push =="
branch=$(git rev-parse --abbrev-ref HEAD)
read -r -p "원격 저장소(origin/$branch)로 push 할까요? (y/n) " push_confirm
if [ "$push_confirm" = "y" ]; then
    git push origin "$branch"
    echo "push 완료."
else
    echo "로컬에만 커밋되었습니다. 나중에 다음 명령으로 올리세요: git push origin $branch"
fi
