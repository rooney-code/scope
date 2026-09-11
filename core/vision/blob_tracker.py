"""프레임 간 연속성을 이용한 블롭 선택기.

여러 후보 블롭이 검출될 때(먼지/노이즈/반사광 등), 이전 프레임 위치와 가장 가깝고
최대 이동 한계(px) 이내인 블롭을 우선 채택한다 - 단순 최근접 게이팅 방식(v1).
칼만 필터 등 고급 추적은 추후 확장 지점.
"""
from __future__ import annotations

import math

from core.vision.red_dot_detector import DetectionResult


class BlobTracker:
    def __init__(self, max_jump_px: float = 60.0) -> None:
        self.max_jump_px = max_jump_px
        self._last_position: tuple[float, float] | None = None

    def reset(self) -> None:
        self._last_position = None

    def select(self, candidates: list[DetectionResult]) -> DetectionResult:
        """후보 목록에서 추적 대상을 선택한다.

        - 이전 위치가 없으면(첫 프레임) 가장 큰 블롭을 선택.
        - 이전 위치가 있으면, max_jump_px 이내에서 가장 가까운 블롭을 선택.
          이내에 아무것도 없으면 찾지 못한 것으로 처리(다음 프레임에서 재탐색).
        """
        if not candidates:
            return DetectionResult(found=False)

        if self._last_position is None:
            best = candidates[0]  # detect()가 area 내림차순으로 정렬해서 줌
            self._last_position = best.center_px
            return best

        lx, ly = self._last_position
        best_candidate = None
        best_dist = math.inf
        for c in candidates:
            cx, cy = c.center_px
            dist = math.hypot(cx - lx, cy - ly)
            if dist < best_dist:
                best_dist = dist
                best_candidate = c

        if best_candidate is None or best_dist > self.max_jump_px:
            return DetectionResult(found=False)

        self._last_position = best_candidate.center_px
        return best_candidate
