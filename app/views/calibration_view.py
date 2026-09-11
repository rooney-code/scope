"""그리드 캘리브레이션 화면: 자동 검출 + 클릭 스냅 + 화살표 미세조정.

작업자는 (1) '자동 검출'로 초기 원점/스케일을 얻고, (2) 화면에서 특정 tick을 클릭한 뒤
그 tick이 나타내는 실제 값(mrad/MOA)을 입력해 스케일을 확정하거나, (3) 화살표 버튼으로
원점을 1px 단위로 미세 조정한다.
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.calibration.grid_auto_detector import GridAutoDetector
from core.calibration.pixel_angle_calibration import PixelAngleCalibration


class ClickableFrameLabel(QLabel):
    clicked_px = Signal(float, float)  # 원본 프레임 좌표계 기준 (x, y)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._frame_size: tuple[int, int] | None = None  # (w, h) of original frame
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(640, 480)

    def set_frame_size(self, w: int, h: int) -> None:
        self._frame_size = (w, h)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._frame_size is None or self.pixmap() is None:
            return
        label_w, label_h = self.width(), self.height()
        frame_w, frame_h = self._frame_size

        # KeepAspectRatio로 스케일된 pixmap이 QLabel 중앙에 배치되므로 letterbox 보정 필요
        scale = min(label_w / frame_w, label_h / frame_h)
        disp_w, disp_h = frame_w * scale, frame_h * scale
        offset_x, offset_y = (label_w - disp_w) / 2, (label_h - disp_h) / 2

        px = (event.position().x() - offset_x) / scale
        py = (event.position().y() - offset_y) / scale
        if 0 <= px <= frame_w and 0 <= py <= frame_h:
            self.clicked_px.emit(px, py)


class CalibrationView(QWidget):
    def __init__(self, calibration: PixelAngleCalibration, camera_id: str, parent=None) -> None:
        super().__init__(parent)
        self.calibration = calibration
        self.camera_id = camera_id
        self.detector = GridAutoDetector()

        self._last_frame: np.ndarray | None = None
        self._last_click_px: tuple[float, float] | None = None

        self.frame_label = ClickableFrameLabel()
        self.frame_label.clicked_px.connect(self._on_frame_clicked)

        self.status_label = QLabel("자동 검출을 실행하거나 이전 캘리브레이션을 불러오세요.")

        auto_btn = QPushButton("그리드 자동 검출")
        auto_btn.clicked.connect(self._on_auto_detect)

        # 클릭 스냅 폼
        snap_group = QGroupBox("클릭 스냅 (화면에서 tick을 클릭한 뒤 값 입력)")
        self.snap_value = QDoubleSpinBox()
        self.snap_value.setRange(-1000, 1000)
        self.snap_value.setValue(10.0)
        self.snap_unit = QComboBox()
        self.snap_unit.addItems(["mrad", "moa"])
        self.snap_axis = QComboBox()
        self.snap_axis.addItems(["x", "y"])
        self.snap_click_label = QLabel("클릭된 위치: 없음")
        snap_apply_btn = QPushButton("이 위치를 tick으로 지정")
        snap_apply_btn.clicked.connect(self._on_snap_apply)

        snap_layout = QGridLayout(snap_group)
        snap_layout.addWidget(QLabel("값"), 0, 0)
        snap_layout.addWidget(self.snap_value, 0, 1)
        snap_layout.addWidget(self.snap_unit, 0, 2)
        snap_layout.addWidget(QLabel("축"), 1, 0)
        snap_layout.addWidget(self.snap_axis, 1, 1)
        snap_layout.addWidget(self.snap_click_label, 2, 0, 1, 3)
        snap_layout.addWidget(snap_apply_btn, 3, 0, 1, 3)

        # 화살표 미세조정: 원점(0.5px 단위)
        ORIGIN_NUDGE_STEP_PX = 0.5
        nudge_group = QGroupBox(f"원점 미세조정 ({ORIGIN_NUDGE_STEP_PX}px)")
        up_btn, down_btn, left_btn, right_btn = (QPushButton(s) for s in ("↑", "↓", "←", "→"))
        up_btn.clicked.connect(lambda: self._on_nudge(0, -ORIGIN_NUDGE_STEP_PX))
        down_btn.clicked.connect(lambda: self._on_nudge(0, ORIGIN_NUDGE_STEP_PX))
        left_btn.clicked.connect(lambda: self._on_nudge(-ORIGIN_NUDGE_STEP_PX, 0))
        right_btn.clicked.connect(lambda: self._on_nudge(ORIGIN_NUDGE_STEP_PX, 0))
        nudge_layout = QGridLayout(nudge_group)
        nudge_layout.addWidget(up_btn, 0, 1)
        nudge_layout.addWidget(left_btn, 1, 0)
        nudge_layout.addWidget(right_btn, 1, 2)
        nudge_layout.addWidget(down_btn, 2, 1)

        # 눈금 간격(px-per-MOA) 미세조정: 0.01 단위, X/Y 축 각각
        # (원점 근처는 잘 맞아도 35MOA 같은 먼 지점에서만 벌어지는 경우가 있어 - 실측으로
        # 확인된 문제 - refine_scale() 자동 추정과 별개로 눈으로 보며 최종 확정하는 용도)
        SCALE_NUDGE_STEP = 0.01
        scale_group = QGroupBox(f"눈금 간격(px/MOA) 미세조정 ({SCALE_NUDGE_STEP})")
        x_minus_btn, x_plus_btn = QPushButton("X −"), QPushButton("X +")
        y_minus_btn, y_plus_btn = QPushButton("Y −"), QPushButton("Y +")
        x_minus_btn.clicked.connect(lambda: self._on_nudge_scale(-SCALE_NUDGE_STEP, 0))
        x_plus_btn.clicked.connect(lambda: self._on_nudge_scale(SCALE_NUDGE_STEP, 0))
        y_minus_btn.clicked.connect(lambda: self._on_nudge_scale(0, -SCALE_NUDGE_STEP))
        y_plus_btn.clicked.connect(lambda: self._on_nudge_scale(0, SCALE_NUDGE_STEP))
        scale_layout = QGridLayout(scale_group)
        scale_layout.addWidget(x_minus_btn, 0, 0)
        scale_layout.addWidget(x_plus_btn, 0, 1)
        scale_layout.addWidget(y_minus_btn, 1, 0)
        scale_layout.addWidget(y_plus_btn, 1, 1)

        save_btn = QPushButton("저장")
        save_btn.clicked.connect(self._on_save)
        load_btn = QPushButton("불러오기")
        load_btn.clicked.connect(self._on_load)

        controls = QVBoxLayout()
        controls.addWidget(auto_btn)
        controls.addWidget(snap_group)
        controls.addWidget(nudge_group)
        controls.addWidget(scale_group)
        controls.addLayout(self._hbox(save_btn, load_btn))
        controls.addWidget(self.status_label)
        controls.addStretch(1)

        root = QHBoxLayout(self)
        root.addWidget(self.frame_label, stretch=2)
        controls_widget = QWidget()
        controls_widget.setLayout(controls)
        root.addWidget(controls_widget, stretch=1)

    @staticmethod
    def _hbox(*widgets) -> QHBoxLayout:
        box = QHBoxLayout()
        for w in widgets:
            box.addWidget(w)
        return box

    # ---- 프레임 표시 ----
    def on_frame(self, frame_bgr: np.ndarray) -> None:
        self._last_frame = frame_bgr
        h, w = frame_bgr.shape[:2]
        self.frame_label.set_frame_size(w, h)
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        self.frame_label.setPixmap(
            QPixmap.fromImage(qimg).scaled(self.frame_label.width(), self.frame_label.height(), Qt.KeepAspectRatio)
        )

    # ---- 액션 ----
    def _on_auto_detect(self) -> None:
        if self._last_frame is None:
            self.status_label.setText("프레임이 없습니다. 카메라가 실행 중인지 확인하세요.")
            return
        result = self.detector.detect(self._last_frame)
        if not result.found:
            self.status_label.setText("자동 검출 실패 - 클릭 스냅으로 수동 설정하세요.")
            return
        self.calibration.seed_from_auto_detection(self.camera_id, result)
        self.status_label.setText(f"자동 검출 완료: 원점={result.origin_px}. 이제 tick 클릭으로 스케일을 확정하세요.")

    def _on_frame_clicked(self, x: float, y: float) -> None:
        self._last_click_px = (x, y)
        self.snap_click_label.setText(f"클릭된 위치: ({x:.1f}, {y:.1f})")

    def _on_snap_apply(self) -> None:
        if self.calibration.profile is None:
            self.status_label.setText("먼저 자동 검출을 실행하세요.")
            return
        if self._last_click_px is None:
            self.status_label.setText("화면에서 tick 위치를 먼저 클릭하세요.")
            return
        axis = self.snap_axis.currentText()
        tick_px = self._last_click_px[0] if axis == "x" else self._last_click_px[1]
        try:
            self.calibration.snap_tick(
                tick_px=tick_px, known_value=self.snap_value.value(), unit=self.snap_unit.currentText(), axis=axis
            )
        except (RuntimeError, ValueError) as exc:
            self.status_label.setText(str(exc))
            return
        p = self.calibration.profile
        self.status_label.setText(f"스냅 완료: px_per_moa_x={p.px_per_moa_x:.3f}, px_per_moa_y={p.px_per_moa_y:.3f}")

    def _on_nudge(self, dx: float, dy: float) -> None:
        if self.calibration.profile is None:
            self.status_label.setText("먼저 자동 검출을 실행하세요.")
            return
        self.calibration.nudge_origin(dx, dy)
        p = self.calibration.profile
        self.status_label.setText(f"원점 조정: ({p.origin_px_x:.1f}, {p.origin_px_y:.1f})")

    def _on_nudge_scale(self, delta_x: float, delta_y: float) -> None:
        if self.calibration.profile is None:
            self.status_label.setText("먼저 자동 검출을 실행하세요.")
            return
        self.calibration.nudge_scale(delta_x, delta_y)
        p = self.calibration.profile
        self.status_label.setText(f"눈금 간격 조정: px_per_moa=({p.px_per_moa_x:.3f}, {p.px_per_moa_y:.3f})")

    def _on_save(self) -> None:
        if self.calibration.profile is None:
            self.status_label.setText("저장할 캘리브레이션이 없습니다.")
            return
        self.calibration.save("calibration_profiles")
        self.status_label.setText("저장 완료.")

    def _on_load(self) -> None:
        if self.calibration.load("calibration_profiles", self.camera_id):
            self.status_label.setText("불러오기 완료.")
        else:
            self.status_label.setText("저장된 캘리브레이션이 없습니다 - 자동 검출부터 진행하세요.")
