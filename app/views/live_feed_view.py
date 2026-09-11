"""카메라 프리뷰 + 오버레이(레드닷 검출 형상, 격자 오버레이는 뷰모델에서 합성) 위젯."""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.vision.red_dot_detector import DetectionResult


class LiveFeedView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._image_label = QLabel("카메라 대기 중...")
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setMinimumSize(640, 480)

        layout = QVBoxLayout(self)
        layout.addWidget(self._image_label)

        self._last_frame: np.ndarray | None = None
        self._last_detection: DetectionResult | None = None
        self._zoom_level = 1  # 1~5

    def set_zoom_level(self, level: int) -> None:
        self._zoom_level = max(1, min(5, level))
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def on_frame(self, frame_bgr: np.ndarray) -> None:
        self._last_frame = frame_bgr
        self._render(frame_bgr, self._last_detection)

    def on_detection(self, result: DetectionResult) -> None:
        self._last_detection = result
        if self._last_frame is not None:
            self._render(self._last_frame, result)

    def _render(self, frame_bgr: np.ndarray, detection: DetectionResult | None) -> None:
        display = frame_bgr.copy()

        # 레드닷 검출 오버레이: 실제 검출된 블롭 형상(컨투어/타원)을 그려 화면 육안 비교 가능하게
        if detection is not None and detection.found:
            if detection.ellipse is not None:
                cv2.ellipse(display, detection.ellipse, (0, 255, 0), 2)
            elif detection.contour is not None:
                cv2.drawContours(display, [detection.contour], -1, (0, 255, 0), 2)

        display = self._apply_zoom(display)

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        self._image_label.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                self._image_label.width(), self._image_label.height(), Qt.KeepAspectRatio
            )
        )

    def _apply_zoom(self, frame: np.ndarray) -> np.ndarray:
        if self._zoom_level <= 1:
            return frame
        h, w = frame.shape[:2]
        factor = self._zoom_level
        crop_w, crop_h = w // factor, h // factor
        cx, cy = w // 2, h // 2
        x0, y0 = max(0, cx - crop_w // 2), max(0, cy - crop_h // 2)
        cropped = frame[y0 : y0 + crop_h, x0 : x0 + crop_w]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
