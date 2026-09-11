"""보어사이터 그리드(십자선 + tick)의 자동 검출.

Hough Line Transform으로 십자선(긴 수평/수직 직선)을 찾아 교차점(원점 후보)을 구하고,
십자선을 따라 짧은 tick들의 위치(px)를 추정한다.

정확도는 완벽하지 않을 수 있으며(조명/각도/텍스트 라벨 간섭), 계획서에 따라 이 결과는
캘리브레이션 UI에서 작업자의 클릭 스냅/화살표 미세조정으로 보정하는 것을 전제로 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class GridDetectionResult:
    found: bool
    origin_px: tuple[float, float] | None = None
    horizontal_angle_deg: float = 0.0  # 0 = 완전 수평
    vertical_angle_deg: float = 0.0  # 90 = 완전 수직
    tick_x_positions_px: list[float] = field(default_factory=list)  # 원점 기준 상대값 아님, 절대 x 좌표
    tick_y_positions_px: list[float] = field(default_factory=list)


class GridAutoDetector:
    def __init__(
        self,
        hough_threshold: int = 80,
        min_line_length_ratio: float = 0.3,  # 프레임 폭/높이 대비 최소 직선 길이 비율
        max_line_gap: int = 10,
        angle_tolerance_deg: float = 3.0,
    ) -> None:
        self.hough_threshold = hough_threshold
        self.min_line_length_ratio = min_line_length_ratio
        self.max_line_gap = max_line_gap
        self.angle_tolerance_deg = angle_tolerance_deg

    def detect(self, frame_bgr: np.ndarray) -> GridDetectionResult:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]

        edges = cv2.Canny(gray, 30, 100)
        min_len = int(min(w, h) * self.min_line_length_ratio)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=self.hough_threshold,
            minLineLength=min_len,
            maxLineGap=self.max_line_gap,
        )
        if lines is None:
            return GridDetectionResult(found=False)

        horizontals = []
        verticals = []
        for line in lines:
            # cv2 버전에 따라 HoughLinesP 반환 shape이 (N,1,4) 또는 (N,4)로 다를 수 있음
            coords = line[0] if line.ndim > 1 else line
            x1, y1, x2, y2 = coords
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            length = np.hypot(x2 - x1, y2 - y1)
            if abs(angle) <= self.angle_tolerance_deg:
                horizontals.append((length, x1, y1, x2, y2, angle))
            elif abs(abs(angle) - 90) <= self.angle_tolerance_deg:
                verticals.append((length, x1, y1, x2, y2, angle))

        if not horizontals or not verticals:
            return GridDetectionResult(found=False)

        horizontals.sort(key=lambda t: t[0], reverse=True)
        verticals.sort(key=lambda t: t[0], reverse=True)
        _, hx1, hy1, hx2, hy2, h_angle = horizontals[0]
        _, vx1, vy1, vx2, vy2, v_angle = verticals[0]

        origin_y = (hy1 + hy2) / 2.0
        origin_x = (vx1 + vx2) / 2.0

        tick_x = self._detect_ticks(gray, axis="x", axis_coord=origin_y)
        tick_y = self._detect_ticks(gray, axis="y", axis_coord=origin_x)

        return GridDetectionResult(
            found=True,
            origin_px=(origin_x, origin_y),
            horizontal_angle_deg=h_angle,
            vertical_angle_deg=v_angle,
            tick_x_positions_px=tick_x,
            tick_y_positions_px=tick_y,
        )

    def _detect_ticks(self, gray: np.ndarray, axis: str, axis_coord: float) -> list[float]:
        """축 선 바로 옆(수직 오프셋)의 얇은 띠에서 어두운 tick 돌출부의 위치를 찾는다.

        단순 구현(v1): 축과 평행한 좁은 띠를 잘라 평균 밝기 프로파일을 구하고,
        국소적으로 어두운 지점(tick)을 피크로 검출한다. 텍스트 라벨과 혼동될 수 있어
        캘리브레이션 UI의 수동 보정이 최종 확정 단계임을 전제로 한다.
        """
        h, w = gray.shape[:2]
        band = 3
        offset = 8  # 축 선 자체를 피해서 살짝 떨어진 위치에서 tick 돌출부를 봄

        if axis == "x":
            y0 = int(max(0, axis_coord - offset - band))
            y1 = int(max(0, axis_coord - offset))
            if y1 <= y0:
                return []
            strip = gray[y0:y1, :]
            profile = strip.mean(axis=0)
        else:
            x0 = int(max(0, axis_coord - offset - band))
            x1 = int(max(0, axis_coord - offset))
            if x1 <= x0:
                return []
            strip = gray[:, x0:x1]
            profile = strip.mean(axis=1)

        return self._find_dark_peaks(profile)

    @staticmethod
    def _find_dark_peaks(profile: np.ndarray, min_gap: int = 5) -> list[float]:
        if profile.size == 0:
            return []
        mean_val = profile.mean()
        threshold = mean_val - profile.std() * 0.5
        dark_mask = profile < threshold

        peaks: list[float] = []
        i = 0
        n = len(dark_mask)
        while i < n:
            if dark_mask[i]:
                j = i
                while j < n and dark_mask[j]:
                    j += 1
                peaks.append((i + j - 1) / 2.0)
                i = j
            else:
                i += 1

        # 너무 인접한 피크 병합
        merged: list[float] = []
        for p in peaks:
            if merged and (p - merged[-1]) < min_gap:
                continue
            merged.append(p)
        return merged
