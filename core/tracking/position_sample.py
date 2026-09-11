"""캘리브레이션 이후(px->MOA 변환 이후)의 위치 시계열 샘플."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSample:
    timestamp_s: float  # time.time() 등 초 단위
    x_moa: float
    y_moa: float
