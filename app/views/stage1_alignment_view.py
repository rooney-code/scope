"""1단계: 렌즈 정렬 게이트. 중심 오차를 실시간 표시하고, 허용 오차 이내로 안정되면
'정렬 완료 / 2단계 준비' 상태를 보여준다."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from core.config.settings import Stage1Settings
from core.tracking.position_sample import PositionSample
from core.tracking.stability_detector import StabilityDetector
from core.vision.red_dot_detector import DetectionResult

_NO_CALIBRATION_MSG = (
    "⚠ 캘리브레이션 필요 - 원점을 찾지 못했습니다. '캘리브레이션' 탭에서 밝은 화면(조리개 최대 상태의 "
    "그리드 이미지)으로 '그리드 자동 검출'을 실행하세요."
)
_SCALE_UNCONFIRMED_MSG = (
    "⚠ 스케일 미확정 - 원점은 찾았지만 눈금 간격이 아직 대략적인 추정치입니다. '캘리브레이션' 탭에서 "
    "빨간 좌표축이 실제 그리드와 맞는지 보면서 '눈금 간격 미세조정' 버튼으로 X/Y 양쪽 축을 맞추세요."
)


class Stage1AlignmentView(QWidget):
    def __init__(self, settings: Stage1Settings, on_ready_to_proceed, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._on_ready_to_proceed = on_ready_to_proceed
        self._stability = StabilityDetector(
            window_size_samples=8, variance_threshold_moa2=0.02, min_stable_duration_ms=settings.stable_duration_ms
        )
        self._calibration = None  # 외부에서 주입 (main_window에서 연결)

        # 캘리브레이션이 안 됐거나 미완성인 채로 시험을 진행하면 화면은 정상처럼 보이는데
        # 실제로는 터무니없는 오차 숫자가 나오는 문제가 있었다(실측으로 확인, 2026-09-14) -
        # 원점을 못 찾았거나 스케일이 아직 확정 안 됐으면 눈에 띄게 경고하고 다음에 뭘 해야
        # 하는지 안내한다.
        self.calibration_warning_label = QLabel(_NO_CALIBRATION_MSG)
        self.calibration_warning_label.setWordWrap(True)
        self.calibration_warning_label.setStyleSheet("color: #b02a2a; font-weight: bold;")

        self.status_label = QLabel("레드닷을 그리드 중앙(0,0)으로 이동하세요.")
        self.offset_label = QLabel("오차: -")
        proceed_btn = QPushButton("2단계로 진행 (자동 활성화됨)")
        proceed_btn.setEnabled(False)
        self._proceed_btn = proceed_btn

        layout = QVBoxLayout(self)
        layout.addWidget(self.calibration_warning_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.offset_label)
        layout.addWidget(proceed_btn)
        layout.addStretch(1)

    def set_calibration(self, calibration) -> None:
        self._calibration = calibration

    def on_detection(self, result: DetectionResult) -> None:
        if self._calibration is None or self._calibration.profile is None:
            self.calibration_warning_label.setText(_NO_CALIBRATION_MSG)
            self.calibration_warning_label.setVisible(True)
            return
        if not self._calibration.is_ready:
            self.calibration_warning_label.setText(_SCALE_UNCONFIRMED_MSG)
            self.calibration_warning_label.setVisible(True)
            return
        self.calibration_warning_label.setVisible(False)

        if not result.found:
            return
        x_moa, y_moa = self._calibration.to_moa(result.center_px)
        offset = (x_moa**2 + y_moa**2) ** 0.5
        self.offset_label.setText(f"오차: x={x_moa:.2f} MOA, y={y_moa:.2f} MOA, 거리={offset:.2f} MOA")

        within_tolerance = offset <= self.settings.tolerance_moa
        # 허용 오차 판정은 거리 기준으로 직접 하고, StabilityDetector는 "값이 흔들리지 않는지"만 확인
        state = self._stability.feed(PositionSample(timestamp_s=time.time(), x_moa=x_moa, y_moa=y_moa))

        if within_tolerance and state.is_stable:
            self.status_label.setText("정렬 완료 - 2단계 준비됨")
            self._proceed_btn.setEnabled(True)
            self._on_ready_to_proceed()
        else:
            self.status_label.setText("레드닷을 그리드 중앙(0,0)으로 이동하세요.")
            self._proceed_btn.setEnabled(False)
