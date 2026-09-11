"""카메라 프리뷰 + 오버레이(레드닷 검출 형상) 위젯.

카메라 해상도는 4K 이상이지만 화면(FHD)에 그대로 축소해서 보여주면 레드닷/눈금 같은 작은
디테일이 뭉개진다. 또한 원점(크로스헤어 교차점)은 카메라 설치 상태에 따라 이미지 중앙이
아닌 임의의 위치에 올 수 있다. 그래서:
  - 확대/자르기는 프레임의 기하학적 중앙이 아니라 **캘리브레이션된 원점**을 기준으로 한다.
  - 자르는 범위는 화면 비율(%)이 아니라 **MOA 단위**로 지정한다(트래블 검사가 35MOA까지
    다루므로 여유를 둔 기본값 45MOA 반경을 보여줌). 확대 슬라이더는 이 기본 범위를 더
    좁히는 배율로 동작한다.
캘리브레이션이 아직 없으면(원점을 모르는 상태) 원본 프레임을 그대로 보여준다.
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from core.calibration.grid_overlay import draw_moa_grid_overlay
from core.calibration.pixel_angle_calibration import PixelAngleCalibration
from core.vision.red_dot_detector import DetectionResult


class LiveFeedView(QWidget):
    def __init__(self, default_view_range_moa: float = 45.0, parent=None) -> None:
        super().__init__(parent)
        self._calibration: PixelAngleCalibration | None = None
        self._default_view_range_moa = default_view_range_moa

        self._image_label = QLabel("카메라 대기 중...")
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setMinimumSize(640, 480)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(1, 5)
        self._zoom_slider.setValue(1)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        self._zoom_label = QLabel(f"확대: 1단계 (원점 기준 ±{default_view_range_moa:.0f}MOA)")

        self._grid_checkbox = QCheckBox("격자형 그리드 표시")
        self._grid_checkbox.stateChanged.connect(self._on_grid_toggle)
        self._grid_overlay_enabled = False

        self._offset_label = QLabel("레드닷 오차: -")

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("확대 단계 (1~5, 원점 기준):"))
        zoom_row.addWidget(self._zoom_slider)
        zoom_row.addWidget(self._zoom_label)
        zoom_row.addWidget(self._grid_checkbox)

        layout = QVBoxLayout(self)
        layout.addWidget(self._image_label)
        layout.addLayout(zoom_row)
        layout.addWidget(self._offset_label)

        self._last_frame: np.ndarray | None = None
        self._last_detection: DetectionResult | None = None
        self._zoom_level = 1  # 1~5, 커질수록 view_range_moa가 좁아짐(더 확대)

    def set_calibration(self, calibration: PixelAngleCalibration) -> None:
        self._calibration = calibration

    def _on_grid_toggle(self, state: int) -> None:
        self._grid_overlay_enabled = bool(state)
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def _on_zoom_changed(self, level: int) -> None:
        self._zoom_level = level
        view_range = self._current_view_range_moa()
        self._zoom_label.setText(f"확대: {level}단계 (원점 기준 ±{view_range:.0f}MOA)")
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def _current_view_range_moa(self) -> float:
        return self._default_view_range_moa / self._zoom_level

    def _current_grid_step_moa(self) -> float:
        """확대 단계와 무관하게 화면에 대략 8~12개 정도의 격자선만 보이도록 간격을 정한다.

        스케일(px/MOA)이 카메라마다 다르므로 1MOA 고정 간격은 배율에 따라 지나치게
        촘촘해 보일 수 있다(체크무늬처럼 됨) - 그래서 view_range_moa에 맞춰 1/2/5/10/20/50 중
        가장 적절한 "보기 좋은" 간격을 고른다.
        """
        target_lines = 10
        raw_step = (self._current_view_range_moa() * 2) / target_lines
        nice_steps = [0.5, 1, 2, 5, 10, 20, 50, 100]
        return min(nice_steps, key=lambda s: abs(s - raw_step))

    def on_frame(self, frame_bgr: np.ndarray) -> None:
        self._last_frame = frame_bgr
        self._render(frame_bgr, self._last_detection)

    def on_detection(self, result: DetectionResult) -> None:
        self._last_detection = result
        if self._last_frame is not None:
            self._render(self._last_frame, result)

    def _render(self, frame_bgr: np.ndarray, detection: DetectionResult | None) -> None:
        display = frame_bgr.copy()
        profile = self._calibration.profile if self._calibration is not None else None

        # 격자형 그리드(1MOA 간격, 사용자 선택으로 켜고 끔) - 원본 프레임 좌표계 기준으로
        # 원점/스케일에 맞춰 그린 뒤 아래에서 함께 크롭한다.
        if self._grid_overlay_enabled and profile is not None:
            display = draw_moa_grid_overlay(
                display,
                profile,
                moa_step=self._current_grid_step_moa(),
                thickness=1,
                max_moa_range=self._current_view_range_moa(),
            )

        # 원점: 작은 마커만 표시 (긴 십자선은 그리지 않음)
        if profile is not None:
            ox, oy = int(round(profile.origin_px_x)), int(round(profile.origin_px_y))
            cv2.drawMarker(display, (ox, oy), (0, 0, 255), cv2.MARKER_CROSS, 16, 1)
            cv2.circle(display, (ox, oy), 5, (0, 0, 255), 1)

        # 레드닷: 검출된 중심점만 작은 점으로 표시 (윤곽선 아님 - 중심 위치 확인이 목적)
        if detection is not None and detection.found:
            dx, dy = int(round(detection.center_px[0])), int(round(detection.center_px[1]))
            cv2.circle(display, (dx, dy), 4, (0, 255, 0), -1)
            cv2.circle(display, (dx, dy), 8, (0, 255, 0), 1)

        display = self._crop_around_origin(display)
        self._draw_offset_text(display, detection, profile)

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        self._image_label.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                self._image_label.width(), self._image_label.height(), Qt.KeepAspectRatio
            )
        )

    def _draw_offset_text(
        self, display: np.ndarray, detection: DetectionResult | None, profile
    ) -> None:
        """레드닷이 원점 기준 좌우/상하로 몇 MOA 벗어나 있는지 화면 좌하단에 표시."""
        if detection is None or not detection.found or profile is None or self._calibration is None:
            self._offset_label.setText("레드닷 오차: -")
            return

        x_moa, y_moa = self._calibration.to_moa(detection.center_px)
        lr = "R" if x_moa >= 0 else "L"
        ud = "U" if y_moa >= 0 else "D"
        text = f"{lr} {abs(x_moa):.2f} MOA  /  {ud} {abs(y_moa):.2f} MOA"

        h, w = display.shape[:2]
        font_scale = max(1.0, w / 1400)  # 해상도에 비례해 글자 크기 자동 조절 (가독성 확보)
        thickness = max(2, int(font_scale * 2))
        (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        pad = 10
        cv2.rectangle(
            display,
            (pad - 6, h - text_h - pad - 10),
            (pad + text_w + 6, h - pad + 6),
            (0, 0, 0),
            -1,
        )
        cv2.putText(
            display, text, (pad, h - pad), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 255), thickness
        )
        self._offset_label.setText(f"레드닷 오차: {text}")

    def _crop_around_origin(self, frame: np.ndarray) -> np.ndarray:
        """캘리브레이션된 원점을 중심으로 ±view_range_moa 영역만 원본 해상도로 잘라낸다.

        캘리브레이션이 아직 없으면(원점 위치를 모름) 원본 프레임을 그대로 반환한다 - 원점을
        모르는 상태에서 프레임 중앙을 임의로 원점처럼 취급하면 실제 설치 상태와 맞지 않는
        위치를 보여주게 되므로 절대 하지 않는다.
        """
        if self._calibration is None or self._calibration.profile is None:
            return frame

        h, w = frame.shape[:2]
        p = self._calibration.profile
        view_range_moa = self._current_view_range_moa()

        half_w_px = view_range_moa * p.px_per_moa_x
        half_h_px = view_range_moa * p.px_per_moa_y

        x0 = int(max(0, p.origin_px_x - half_w_px))
        x1 = int(min(w, p.origin_px_x + half_w_px))
        y0 = int(max(0, p.origin_px_y - half_h_px))
        y1 = int(min(h, p.origin_px_y + half_h_px))

        if x1 <= x0 or y1 <= y0:
            return frame

        cropped = frame[y0:y1, x0:x1]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
