"""프레임 간 연속성을 이용한 블롭 선택기.

여러 후보 블롭이 검출될 때(먼지/노이즈/반사광 등), 이전 프레임 위치와 가장 가깝고
최대 이동 한계(px) 이내인 블롭을 우선 채택한다 - 단순 최근접 게이팅 방식(v1).

추가로, 심한 "코멧테일" 왜곡 구간(elongation이 임계값을 넘는 프레임)에서만 이전
프레임들로 추정한 속도 기반 예측 위치와 현재 측정치를 blend해 흔들림을 줄인다
(사용자 확인 사항, 2026-09-13 - docs/detection_notes.md 10차 참고). 정상적인 원형
구간(대부분의 프레임)은 이 보정이 적용되지 않고 프레임별 측정치를 그대로 신뢰한다.
"""
from __future__ import annotations

import math
from dataclasses import replace

from core.vision.red_dot_detector import DetectionResult


class BlobTracker:
    def __init__(
        self,
        max_jump_px: float = 60.0,
        elongation_correction_threshold: float = 1.5,
        elongation_correction_blend: float = 0.5,
    ) -> None:
        self.max_jump_px = max_jump_px
        self.elongation_correction_threshold = elongation_correction_threshold
        self.elongation_correction_blend = elongation_correction_blend
        self._last_position: tuple[float, float] | None = None
        self._prev_position: tuple[float, float] | None = None

    def reset(self) -> None:
        self._last_position = None
        self._prev_position = None

    def select(self, candidates: list[DetectionResult]) -> DetectionResult:
        """후보 목록에서 추적 대상을 선택한다.

        - 이전 위치가 없으면(첫 프레임) 가장 큰 블롭을 선택.
        - 이전 위치가 있으면, max_jump_px 이내에서 가장 가까운 블롭을 선택.
          이내에 아무것도 없으면 찾지 못한 것으로 처리(다음 프레임에서 재탐색).
        - 채택된 블롭의 elongation이 임계값을 넘으면(심한 코멧테일 왜곡), 이전 속도로
          예측한 위치와 blend해 최종 좌표를 보정한다.
        """
        if not candidates:
            return DetectionResult(found=False)

        if self._last_position is None:
            best = candidates[0]  # detect()가 area 내림차순으로 정렬해서 줌
            self._prev_position = None
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

        result = self._apply_motion_correction(best_candidate)
        self._prev_position = self._last_position
        self._last_position = result.center_px
        return result

    def _apply_motion_correction(self, candidate: DetectionResult) -> DetectionResult:
        elongation = self._elongation(candidate)
        if (
            elongation is None
            or elongation < self.elongation_correction_threshold
            or self._prev_position is None
        ):
            return candidate

        lx, ly = self._last_position
        px, py = self._prev_position
        predicted = (lx + (lx - px), ly + (ly - py))

        mx, my = candidate.center_px
        w = self.elongation_correction_blend
        blended = (mx * (1 - w) + predicted[0] * w, my * (1 - w) + predicted[1] * w)
        return replace(candidate, center_px=blended)

    @staticmethod
    def _elongation(result: DetectionResult) -> float | None:
        if result.ellipse is None:
            return None
        major, minor = result.ellipse[1]
        if minor <= 0:
            return None
        return max(major, minor) / min(major, minor)
