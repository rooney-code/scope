"""저장된 캘리브레이션(원점+px-per-MOA)을 이용한 가상 격자 오버레이.

라이브 영상에서는 매 프레임 그리드를 재검출하지 않는다 - 캘리브레이션 시점(조리개 열림,
가장 잘 보이는 상태)에서 1회 확정한 원점/스케일을 재사용해 1MOA 단위 격자를 합성해서 그린다.
작업자가 라이브 뷰에서 켜고 끌 수 있는 토글 오버레이로 사용.
"""
from __future__ import annotations

import cv2
import numpy as np

from core.calibration.pixel_angle_calibration import CalibrationProfile


def draw_moa_grid_overlay(
    frame_bgr: np.ndarray,
    profile: CalibrationProfile,
    moa_step: float = 1.0,
    color_bgr: tuple[int, int, int] = (0, 200, 255),
    thickness: int = 1,
    max_moa_range: float = 40.0,
) -> np.ndarray:
    """frame_bgr 위에 moa_step 간격의 격자선을 그려서 새 배열로 반환한다(원본 불변)."""
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    ox, oy = profile.origin_px_x, profile.origin_px_y

    steps = int(max_moa_range / moa_step)
    for i in range(-steps, steps + 1):
        m = i * moa_step
        x_px = int(round(ox + m * profile.px_per_moa_x))
        if 0 <= x_px < w:
            line_color = color_bgr if m != 0 else (0, 0, 255)
            cv2.line(out, (x_px, 0), (x_px, h), line_color, thickness)

        y_px = int(round(oy - m * profile.px_per_moa_y))  # 화면 y 반전 (위 = +Y)
        if 0 <= y_px < h:
            line_color = color_bgr if m != 0 else (0, 0, 255)
            cv2.line(out, (0, y_px), (w, y_px), line_color, thickness)

    return out
