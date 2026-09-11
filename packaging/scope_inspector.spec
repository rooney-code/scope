# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - 폴더형(onedir) 배포 빌드.

사용법 (프로젝트 최상위 폴더에서):
    pyinstaller packaging\\scope_inspector.spec

문제 진단이 필요하면 아래 console=False 를 True로 바꿔서 다시 빌드하면
콘솔 창에 파이썬 예외 메시지가 그대로 출력됩니다.
"""

import sys
from pathlib import Path

block_cipher = None

# 이 spec 파일은 packaging/ 폴더 안에 있으므로 프로젝트 루트는 한 단계 위
PROJECT_ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(PROJECT_ROOT / "settings.json"), "."),
    (str(PROJECT_ROOT / "core" / "data" / "schema.sql"), "core/data"),
    (str(PROJECT_ROOT / "docs"), "docs"),
]

hiddenimports = [
    # PySide6는 보통 PyInstaller 훅이 자동으로 처리하지만, 일부 배포 환경에서
    # 플랫폼 플러그인이 누락되는 경우가 있어 명시적으로 추가.
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "cv2",
    "numpy",
    "pyodbc",
]

# ids_peak / ids_peak_ipl은 IDS Software Suite가 설치된 PC에만 존재하는 선택적 패키지.
# 빌드 PC에 설치되어 있으면 자동으로 포함되고, 없으면 무시된다(런타임에 --mock/--playback으로도
# 동작 가능하므로 빌드 실패 조건으로 취급하지 않음).
try:
    import ids_peak  # noqa: F401

    hiddenimports += ["ids_peak", "ids_peak_ipl"]
except ImportError:
    pass

a = Analysis(
    [str(PROJECT_ROOT / "app" / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScopeInspector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 문제 진단 시 True로 바꿔서 재빌드하면 콘솔에 에러 메시지가 출력됨
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ScopeInspector",
)
