"""저장된 캘리브레이션(원점+px-per-MOA)을 이용한 좌표축/안내선 오버레이.

라이브 영상에서는 매 프레임 그리드를 재검출하지 않는다 - 캘리브레이션 시점(조리개 열림,
가장 잘 보이는 상태)에서 1회 확정한 원점/스케일을 재사용해 오버레이를 합성해서 그린다.
작업자가 라이브 뷰에서 켜고 끌 수 있는 토글로 사용.

촘촘한 격자(체크무늬처럼 보여 오히려 가독성이 떨어짐, 사용자 피드백으로 폐기)가 아니라:
  - 원점을 지나는 빨간 좌표축 2개(가로/세로) + MOA 눈금/라벨
  - 레드닷 위치를 지나는 안내선 2개(가로/세로) - 좌표축과 만나는 지점을 보면 레드닷의
    좌표를 바로 읽을 수 있음
두 함수로 나눠서 그린다.
"""
from __future__ import annotations

import cv2
import numpy as np

from core.calibration.pixel_angle_calibration import CalibrationProfile

_AXIS_COLOR_BGR = (0, 0, 255)  # 빨강
_GUIDE_COLOR_BGR = (0, 200, 255)  # 주황/노랑 계열 - 좌표축과 구분되게


def draw_coordinate_axes(
    frame_bgr: np.ndarray,
    profile: CalibrationProfile,
    moa_step: float = 10.0,
    color_bgr: tuple[int, int, int] = _AXIS_COLOR_BGR,
    thickness: int = 2,
    tick_length_px: int = 8,
    max_moa_range: float = 45.0,
    draw_labels: bool = True,
) -> np.ndarray:
    """원점을 지나는 좌표축 2개(가로/세로)와 MOA 눈금(작은 틱 + 숫자 라벨)만 그린다.

    촘촘한 격자 대신 좌표축 하나씩만 그려서, 눈금을 보고 위치를 가늠할 수 있게 하되
    화면을 뒤덮는 체크무늬가 되지 않게 한다. 숫자 라벨은 cv2.putText가 한글 글리프를
    지원하지 않으므로 ASCII 숫자만 사용한다.
    """
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    ox, oy = profile.origin_px_x, profile.origin_px_y

    ox_i, oy_i = int(round(ox)), int(round(oy))
    cv2.line(out, (0, oy_i), (w, oy_i), color_bgr, thickness)
    cv2.line(out, (ox_i, 0), (ox_i, h), color_bgr, thickness)

    steps = int(max_moa_range / moa_step)
    font_scale = max(0.4, min(1.0, w / 2400))
    for i in range(-steps, steps + 1):
        if i == 0:
            continue
        m = i * moa_step

        x_px = int(round(ox + m * profile.px_per_moa_x))
        if 0 <= x_px < w:
            cv2.line(out, (x_px, oy_i - tick_length_px), (x_px, oy_i + tick_length_px), color_bgr, thickness)
            if draw_labels:
                cv2.putText(
                    out, f"{m:g}", (x_px + 3, oy_i - tick_length_px - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, color_bgr, 1,
                )

        y_px = int(round(oy - m * profile.px_per_moa_y))  # 화면 y 반전 (위 = +Y)
        if 0 <= y_px < h:
            cv2.line(out, (ox_i - tick_length_px, y_px), (ox_i + tick_length_px, y_px), color_bgr, thickness)
            if draw_labels:
                cv2.putText(
                    out, f"{m:g}", (ox_i + tick_length_px + 3, y_px + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, color_bgr, 1,
                )

    return out


def draw_dot_guide_lines(
    frame_bgr: np.ndarray,
    dot_px: tuple[float, float],
    color_bgr: tuple[int, int, int] = _GUIDE_COLOR_BGR,
    thickness: int = 2,
) -> np.ndarray:
    """레드닷 위치를 지나는 가로/세로 안내선을 그린다 - 좌표축과 만나는 지점을 보면
    레드닷의 현재 좌표(MOA)를 눈으로 바로 읽을 수 있다."""
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    dx, dy = int(round(dot_px[0])), int(round(dot_px[1]))
    cv2.line(out, (0, dy), (w, dy), color_bgr, thickness)
    cv2.line(out, (dx, 0), (dx, h), color_bgr, thickness)
    return out
