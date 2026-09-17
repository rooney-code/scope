"""사용자가 직접 편집/확인해야 하는 파일들(settings.json, reports/ 등)이 있어야 할 위치를
계산한다 - 개발 환경(소스 실행)과 배포용 실행 파일(PyInstaller exe) 양쪽에서 일관되게
"실행 파일이 있는 폴더"를 가리키게 하기 위함.

PyInstaller로 빌드하면(특히 최신 버전의 onedir 모드) 파이썬 소스는 exe와 같은 폴더가 아니라
그 안의 _internal/ 서브폴더에 풀려서 실행된다 - `Path(__file__)` 기준으로 경로를 계산하면
그 _internal/ 안에 reports/를 만들거나 settings.json을 찾게 되어, 사용자가 exe 옆에 둔
설정 파일은 무시되고 결과 파일도 못 찾을 곳에 쌓이는 문제가 있었다(실측으로 확인, 2026-09-16:
dist\\ScopeInspector\\_internal\\reports\\에 저장됨 - packaging_guide.md는 exe 옆에 두라고
안내하고 있었는데 실제 동작은 그렇지 않았음). `sys.frozen`이면 항상 실행 파일(`sys.executable`)이
있는 폴더를 기준으로 삼는다.
"""
from __future__ import annotations

import sys
from pathlib import Path


def get_app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # 개발 환경: 이 파일(core/app_paths.py) 기준 프로젝트 루트(한 단계 위).
    return Path(__file__).resolve().parents[1]
