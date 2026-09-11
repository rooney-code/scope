"""카메라 프리뷰 + 오버레이(원점, 레드닷 중심, 선택적 좌표축/안내선) 위젯.

카메라 해상도는 4K 이상이지만 화면(FHD)에 그대로 축소해서 보여주면 레드닷/눈금 같은 작은
디테일이 뭉개진다. 또한 원점(크로스헤어 교차점)은 카메라 설치 상태에 따라 이미지 중앙이
아닌 임의의 위치에 올 수 있다. 그래서:
  - 확대/자르기는 프레임의 기하학적 중앙이 아니라 **캘리브레이션된 원점**을 기준으로 한다.
  - 자르는 범위는 화면 비율(%)이 아니라 **MOA 단위**로 지정한다(트래블 검사가 35MOA까지
    다루므로 여유를 둔 기본값 45MOA 반경을 보여줌). 확대 슬라이더는 이 기본 범위를 더
    좁히는 배율로 동작한다.
  - 자른 영역은 **자기 자신의 가로세로 비율을 유지**한 채로만 리사이즈한다(원본 프레임의
    가로세로 비율로 강제로 맞추면 좌우가 늘어나 보이는 왜곡이 생김 - 실측으로 확인된 버그).
  - 원점/레드닷 마커, 좌표축/안내선 오버레이는 모두 **자르고 리사이즈까지 끝난 최종 화면
    좌표계**에서 그린다. 원본 해상도에서 그린 뒤 리사이즈하면 1px 두께 선이 보간 과정에서
    위치에 따라 보였다 안 보였다 하는 문제가 있었음(실측으로 확인된 버그).
  - 최종 QLabel 표시 시 `Qt.SmoothTransformation`을 사용한다 - 기본(FastTransformation,
    최근접이웃)은 축소 비율에 따라 얇은 선을 통째로 건너뛰어 사라지게 하는 경우가 있었음
    (실측으로 확인된 버그, 선 두께도 여유있게 2px로 상향).
  - 격자형(체크무늬) 오버레이 대신, 원점을 지나는 **좌표축 2개(MOA 눈금 포함)** + 레드닷
    위치를 지나는 **안내선 2개**만 그린다 - 안내선이 좌표축과 만나는 지점을 보면 레드닷의
    좌표를 바로 읽을 수 있다(사용자 피드백으로 촘촘한 격자에서 변경).
캘리브레이션이 아직 없으면(원점을 모르는 상태) 원본 프레임을 그대로 보여준다.
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QSlider, QVBoxLayout, QWidget

from core.calibration.grid_overlay import draw_coordinate_axes, draw_dot_guide_lines
from core.calibration.pixel_angle_calibration import CalibrationProfile, PixelAngleCalibration
from core.vision.red_dot_detector import DetectionResult

_ORIGIN_MARKER_COLOR_BGR = (255, 200, 0)  # 하늘색 계열 - 레드닷(빨강)과 구분되게
_RED_DOT_MARKER_COLOR_BGR = (0, 0, 255)  # 레드닷 중심 표시: 빨간 점 하나


class _ClickableImageLabel(QLabel):
    """라벨 위 클릭 위치(라벨 로컬 좌표)를 그대로 알려주는 QLabel - 원본 프레임 좌표로의
    역변환은 LiveFeedView가 담당한다(크롭/리사이즈 단계를 알고 있는 쪽이 LiveFeedView이므로)."""

    clicked = Signal(float, float)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        self.clicked.emit(event.position().x(), event.position().y())


class LiveFeedView(QWidget):
    # 원본 프레임 좌표계 기준 클릭 위치 (x, y) - 캘리브레이션 모드에서만 발생(set_calibration_mode)
    frame_clicked_px = Signal(float, float)

    def __init__(self, default_view_range_moa: float = 45.0, display_target_size: int = 1600, parent=None) -> None:
        super().__init__(parent)
        self._calibration: PixelAngleCalibration | None = None
        self._default_view_range_moa = default_view_range_moa
        self._display_target_size = display_target_size
        self._calibration_mode = False  # True: 크롭/오버레이 없이 원본 전체 + 클릭 스냅 가능
        self._last_display_size: tuple[int, int] | None = None
        self._last_transform: tuple[float, float, float] | None = None

        self._image_label = _ClickableImageLabel("카메라 대기 중...")
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setMinimumSize(200, 200)
        self._image_label.clicked.connect(self._on_image_clicked)

        # 영상이 거의 정사각(3088x2076)이라 표시 영역도 정사각으로 강제한다. QLabel의
        # Qt.KeepAspectRatio는 "그 안의 pixmap"만 비율을 지키는 것이라, 라벨 자체가
        # 좌우로 넓은 직사각형으로 배치되면 위아래로 여백만 큰 작은 정사각형이 되어 버림
        # (실측 확인). 그래서 라벨을 감싸는 컨테이너에 stretch=1을 줘 남는 공간을 전부
        # 차지하게 하고, resizeEvent에서 그 컨테이너의 짧은 쪽 길이로 라벨을 정사각
        # 고정크기로 강제한다.
        self._image_container = QWidget()
        image_container_layout = QHBoxLayout(self._image_container)
        image_container_layout.setContentsMargins(0, 0, 0, 0)
        image_container_layout.addStretch(1)
        image_container_layout.addWidget(self._image_label)
        image_container_layout.addStretch(1)
        self._image_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(1, 5)
        self._zoom_slider.setValue(1)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        self._zoom_label = QLabel(f"확대: 1단계 (원점 기준 ±{default_view_range_moa:.0f}MOA)")

        self._grid_checkbox = QCheckBox("좌표축/안내선 표시")
        self._grid_checkbox.stateChanged.connect(self._on_grid_toggle)
        self._grid_overlay_enabled = False

        self._offset_label = QLabel("레드닷 오차: -")

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("확대 단계 (1~5, 원점 기준):"))
        zoom_row.addWidget(self._zoom_slider)
        zoom_row.addWidget(self._zoom_label)
        zoom_row.addWidget(self._grid_checkbox)

        layout = QVBoxLayout(self)
        layout.addWidget(self._image_container, stretch=1)
        layout.addLayout(zoom_row)
        layout.addWidget(self._offset_label)

        self._last_frame: np.ndarray | None = None
        self._last_detection: DetectionResult | None = None
        self._zoom_level = 1  # 1~5, 커질수록 view_range_moa가 좁아짐(더 확대)
        self._relayout_square_image()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout_square_image()

    def _relayout_square_image(self) -> None:
        side = max(200, min(self._image_container.width(), self._image_container.height()))
        self._image_label.setFixedSize(side, side)
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def set_calibration(self, calibration: PixelAngleCalibration) -> None:
        self._calibration = calibration

    def set_calibration_mode(self, enabled: bool) -> None:
        """캘리브레이션 탭이 활성화됐을 때 켠다 - 크롭/격자오버레이 없이 원본 프레임 전체를
        보여주고(먼 tick도 클릭 가능해야 하므로), 클릭 시 frame_clicked_px로 원본 좌표를 알려준다."""
        self._calibration_mode = enabled
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def _on_image_clicked(self, label_x: float, label_y: float) -> None:
        if not self._calibration_mode or self._last_display_size is None:
            return
        disp_w, disp_h = self._last_display_size
        label_w, label_h = self._image_label.width(), self._image_label.height()
        if disp_w <= 0 or disp_h <= 0 or label_w <= 0 or label_h <= 0:
            return

        # QLabel.setPixmap(..., Qt.KeepAspectRatio)로 인한 letterbox 보정 (ClickableFrameLabel과 동일 원리)
        label_scale = min(label_w / disp_w, label_h / disp_h)
        offset_x = (label_w - disp_w * label_scale) / 2
        offset_y = (label_h - disp_h * label_scale) / 2
        disp_x = (label_x - offset_x) / label_scale
        disp_y = (label_y - offset_y) / label_scale
        if not (0 <= disp_x <= disp_w and 0 <= disp_y <= disp_h):
            return  # letterbox 여백 클릭은 무시

        if self._last_transform is None:
            orig_x, orig_y = disp_x, disp_y
        else:
            x0, y0, scale = self._last_transform
            orig_x, orig_y = disp_x / scale + x0, disp_y / scale + y0
        self.frame_clicked_px.emit(orig_x, orig_y)

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

    def _current_axis_label_step_moa(self) -> float:
        """숫자 라벨은 대략 8~12개 정도만 보이도록 간격을 정한다(눈금 자체는 1MOA 고정).

        스케일(px/MOA)이 카메라마다 다르므로 숫자 라벨까지 view_range와 무관하게
        고정하면 배율에 따라 너무 빽빽하거나 너무 뜸해질 수 있다 - view_range_moa에 맞춰
        1/2/5/10/20/50 중 가장 적절한 라벨 간격을 고른다(1MOA 눈금 간격의 배수가 되도록).
        """
        target_labels = 10
        raw_step = (self._current_view_range_moa() * 2) / target_labels
        nice_steps = [1, 2, 5, 10, 20, 50, 100]
        return min(nice_steps, key=lambda s: abs(s - raw_step))

    def on_frame(self, frame_bgr: np.ndarray) -> None:
        self._last_frame = frame_bgr
        self._render(frame_bgr, self._last_detection)

    def on_detection(self, result: DetectionResult) -> None:
        self._last_detection = result
        if self._last_frame is not None:
            self._render(self._last_frame, result)

    def _render(self, frame_bgr: np.ndarray, detection: DetectionResult | None) -> None:
        profile = self._calibration.profile if self._calibration is not None else None

        if self._calibration_mode:
            # 캘리브레이션은 먼 tick(예: ±35MOA 근처)도 클릭해야 하므로 크롭하지 않고 원본
            # 전체를 보여준다 - 오버레이(격자/원점/레드닷/오차 텍스트)도 그리지 않는다.
            display, transform = self._scale_to_display_size(frame_bgr)
        else:
            display, transform = self._crop_and_resize_around_origin(frame_bgr, profile)

        self._last_display_size = (display.shape[1], display.shape[0])
        self._last_transform = transform

        if self._calibration_mode:
            rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            self._image_label.setPixmap(
                QPixmap.fromImage(qimg).scaled(
                    self._image_label.width(), self._image_label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
            return

        display_profile = self._transform_profile(profile, transform)

        dot_px_display: tuple[float, float] | None = None
        if detection is not None and detection.found:
            dot_px_display = self._transform_point(detection.center_px, transform)

        if self._grid_overlay_enabled and display_profile is not None:
            display = draw_coordinate_axes(
                display,
                display_profile,
                tick_step_moa=1.0,
                label_step_moa=self._current_axis_label_step_moa(),
                max_moa_range=self._current_view_range_moa(),
            )
            if dot_px_display is not None:
                display = draw_dot_guide_lines(display, dot_px_display)

        # 원점: 작은 마커만 표시 (좌표축이 꺼져 있어도 항상 보이게, 레드닷과 다른 색으로 구분)
        if display_profile is not None:
            ox, oy = int(round(display_profile.origin_px_x)), int(round(display_profile.origin_px_y))
            cv2.drawMarker(display, (ox, oy), _ORIGIN_MARKER_COLOR_BGR, cv2.MARKER_CROSS, 14, 2)

        # 레드닷: 검출된 중심점을 빨간 점 하나로만 표시
        if dot_px_display is not None:
            cv2.circle(display, (int(round(dot_px_display[0])), int(round(dot_px_display[1]))), 4, _RED_DOT_MARKER_COLOR_BGR, -1)

        self._draw_offset_text(display, detection, profile)

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        self._image_label.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                self._image_label.width(),
                self._image_label.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,  # 기본(FastTransformation, 최근접이웃)은 1px 안내선을
                # 축소 과정에서 통째로 건너뛰어 사라지게 하는 경우가 있어 스무스 스케일링 사용
            )
        )

    def _draw_offset_text(
        self, display: np.ndarray, detection: DetectionResult | None, profile
    ) -> None:
        """레드닷이 원점 기준 좌우/상하로 몇 MOA 벗어나 있는지 화면 좌하단에 표시.

        cv2.putText는 한글 글리프가 없어 깨지므로 영문 라벨(R/L/U/D)만 사용한다.
        """
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

    def _scale_to_display_size(self, frame: np.ndarray) -> tuple[np.ndarray, tuple[float, float, float]]:
        """캘리브레이션 모드용: 크롭 없이 원본 전체를 display_target_size에 맞게 비율 유지
        축소만 한다(원거리 tick도 화면에 보이고 클릭 가능해야 하므로)."""
        h, w = frame.shape[:2]
        scale = self._display_target_size / max(w, h)
        new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        return resized, (0.0, 0.0, scale)

    def _crop_and_resize_around_origin(
        self, frame: np.ndarray, profile: CalibrationProfile | None
    ) -> tuple[np.ndarray, tuple[float, float, float] | None]:
        """캘리브레이션된 원점을 중심으로 ±view_range_moa 영역만 원본 해상도로 잘라낸다.

        캘리브레이션이 아직 없으면(원점 위치를 모름) 원본 프레임을 그대로 반환한다 - 원점을
        모르는 상태에서 프레임 중앙을 임의로 원점처럼 취급하면 실제 설치 상태와 맞지 않는
        위치를 보여주게 되므로 절대 하지 않는다.

        반환값: (표시용 이미지, transform). transform은 (x0, y0, scale) - 원본 프레임 좌표
        (px, py)를 표시용 좌표로 변환하려면 ((px-x0)*scale, (py-y0)*scale). 크롭이 적용되지
        않았으면 transform은 None(=원본 좌표를 그대로 사용).
        """
        if profile is None:
            return frame, None

        h, w = frame.shape[:2]
        view_range_moa = self._current_view_range_moa()

        half_w_px = view_range_moa * profile.px_per_moa_x
        half_h_px = view_range_moa * profile.px_per_moa_y

        x0 = int(max(0, profile.origin_px_x - half_w_px))
        x1 = int(min(w, profile.origin_px_x + half_w_px))
        y0 = int(max(0, profile.origin_px_y - half_h_px))
        y1 = int(min(h, profile.origin_px_y + half_h_px))

        if x1 <= x0 or y1 <= y0:
            return frame, None

        cropped = frame[y0:y1, x0:x1]
        crop_h, crop_w = cropped.shape[:2]

        # 자기 자신의 가로세로 비율을 유지한 채로만 확대/축소 (원본 프레임 크기에 억지로
        # 맞추면 좌우 또는 상하가 늘어나 보이는 왜곡이 생김)
        scale = self._display_target_size / max(crop_w, crop_h)
        new_w, new_h = max(1, int(round(crop_w * scale))), max(1, int(round(crop_h * scale)))
        resized = cv2.resize(cropped, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        return resized, (float(x0), float(y0), scale)

    @staticmethod
    def _transform_point(point_px: tuple[float, float], transform: tuple[float, float, float] | None) -> tuple[float, float]:
        if transform is None:
            return point_px
        x0, y0, scale = transform
        return (point_px[0] - x0) * scale, (point_px[1] - y0) * scale

    @classmethod
    def _transform_profile(
        cls, profile: CalibrationProfile | None, transform: tuple[float, float, float] | None
    ) -> CalibrationProfile | None:
        """캘리브레이션 프로파일(원점+스케일)을 크롭/리사이즈 후의 표시 좌표계로 변환한
        새 프로파일을 만든다 - 격자/원점 마커를 최종 화면 좌표계에서 그리기 위함."""
        if profile is None:
            return None
        if transform is None:
            return profile

        ox, oy = cls._transform_point((profile.origin_px_x, profile.origin_px_y), transform)
        scale = transform[2]
        return CalibrationProfile(
            camera_id=profile.camera_id,
            origin_px_x=ox,
            origin_px_y=oy,
            px_per_moa_x=profile.px_per_moa_x * scale,
            px_per_moa_y=profile.px_per_moa_y * scale,
        )
