"""시험 결과를 텍스트 파일로 저장 - DB는 기능 검증 후 별도로 다시 붙이기로 하고, 지금은
배포 대상 PC에 DB 설치 없이도 시험 종료 시 결과를 남길 수 있게 하기 위한 임시 조치
(사용자 요청, 2026-09-16). `InspectionRepository`(core/data/repository.py)는 그대로 남겨둬서
나중에 DB를 다시 붙일 때 같은 InspectionSession을 그대로 쓸 수 있다.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from core.inspection.models import InspectionSession

_DEFAULT_REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def write_session_txt(session: InspectionSession, directory: Path | None = None) -> Path:
    """세션 결과를 사람이 읽기 쉬운 텍스트로 저장하고, 저장된 파일 경로를 반환한다.
    파일명: {부품ID}_{YYYYMMDD_HHMMSS}.txt (디렉터리 없으면 생성)."""
    out_dir = directory if directory is not None else _DEFAULT_REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now()
    safe_scope_id = _UNSAFE_FILENAME_CHARS.sub("_", session.scope_id) or "unknown"
    path = out_dir / f"{safe_scope_id}_{timestamp:%Y%m%d_%H%M%S}.txt"

    path.write_text(_render(session, timestamp), encoding="utf-8")
    return path


def _render(session: InspectionSession, timestamp: datetime) -> str:
    lines = [
        f"부품 ID: {session.scope_id}",
        f"저장 시각: {timestamp:%Y-%m-%d %H:%M:%S}",
        f"전체 판정: {session.overall_verdict.value}",
        "",
    ]
    for result in session.direction_results:
        lines.append(f"[{result.direction.value}] 시도 {result.attempt_number}회차 - {result.verdict.value}")
        if result.start_point_moa is not None:
            sx, sy = result.start_point_moa
            lines.append(f"  시작 위치: x={sx:.2f} MOA, y={sy:.2f} MOA")
        for check in result.check_results:
            measured = f"{check.measured_value:.2f}" if check.measured_value is not None else "-"
            threshold = f"{check.threshold_used:.2f}" if check.threshold_used is not None else "-"
            lines.append(f"  {check.check_type.value}: 측정={measured} 기준={threshold} -> {check.status.value}")
        lines.append("")
    return "\n".join(lines)
