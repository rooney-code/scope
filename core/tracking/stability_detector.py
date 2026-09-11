"""정지구간(안정 상태) 감지.

위치 시계열의 최근 N개 샘플의 분산이 임계값 이하로 min_stable_duration_ms 동안 유지되면
"정지" 상태로 판단한다. 1단계 정렬 게이트, 드리프트 측정 시점, 원점 복귀 감지에 공통 사용.

v1은 단순 슬라이딩 윈도우 분산 기반이며 칼만 필터 등 고급 기법은 사용하지 않는다.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass

from core.tracking.position_sample import PositionSample


@dataclass
class StabilityState:
    is_stable: bool
    stable_position: tuple[float, float] | None = None  # (x_moa, y_moa) 평균, 안정 상태일 때만


class StabilityDetector:
    def __init__(
        self,
        window_size_samples: int = 8,
        variance_threshold_moa2: float = 0.01,
        min_stable_duration_ms: float = 150.0,
    ) -> None:
        self.window_size_samples = window_size_samples
        self.variance_threshold_moa2 = variance_threshold_moa2
        self.min_stable_duration_ms = min_stable_duration_ms
        self._window: deque[PositionSample] = deque(maxlen=window_size_samples)
        self._stable_since_s: float | None = None

    def reset(self) -> None:
        self._window.clear()
        self._stable_since_s = None

    def feed(self, sample: PositionSample) -> StabilityState:
        self._window.append(sample)

        if len(self._window) < self.window_size_samples:
            self._stable_since_s = None
            return StabilityState(is_stable=False)

        xs = [s.x_moa for s in self._window]
        ys = [s.y_moa for s in self._window]
        variance = statistics.pvariance(xs) + statistics.pvariance(ys)

        if variance > self.variance_threshold_moa2:
            self._stable_since_s = None
            return StabilityState(is_stable=False)

        if self._stable_since_s is None:
            self._stable_since_s = sample.timestamp_s

        duration_ms = (sample.timestamp_s - self._stable_since_s) * 1000.0
        if duration_ms < self.min_stable_duration_ms:
            return StabilityState(is_stable=False)

        return StabilityState(is_stable=True, stable_position=(statistics.mean(xs), statistics.mean(ys)))
