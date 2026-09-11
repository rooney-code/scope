"""앱 셸: 부품ID 입력 게이트 + 탭 네비게이션(캘리브레이션/카메라설정/1단계/2단계/결과)."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from app.views.calibration_view import CalibrationView
from app.views.camera_settings_view import CameraSettingsView
from app.views.live_feed_view import LiveFeedView
from app.views.results_view import ResultsView
from app.views.stage1_alignment_view import Stage1AlignmentView
from app.views.stage2_travel_test_view import Stage2TravelTestView


class MainWindow(QMainWindow):
    def __init__(self, viewmodel: InspectionViewModel, camera_id: str, repository=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("조준경 불량 검사 프로그램")
        self.vm = viewmodel

        # 부품 ID 게이트
        self.scope_id_input = QLineEdit()
        self.scope_id_input.setPlaceholderText("부품(조준경) ID 입력 - 미입력 시 검사 시작 불가")
        self.scope_id_input.textChanged.connect(self._on_scope_id_changed)
        gate_layout = QHBoxLayout()
        gate_layout.addWidget(QLabel("부품 ID:"))
        gate_layout.addWidget(self.scope_id_input)

        self.live_feed_view = LiveFeedView()
        self.live_feed_view.set_calibration(self.vm.calibration)
        self.calibration_view = CalibrationView(self.vm.calibration, camera_id)
        self.camera_settings_view = CameraSettingsView(self.vm.camera, self.vm.settings.camera)
        self.stage1_view = Stage1AlignmentView(self.vm.settings.stage1, on_ready_to_proceed=self._on_stage1_ready)
        self.stage1_view.set_calibration(self.vm.calibration)
        self.stage2_view = Stage2TravelTestView(self.vm)
        self.results_view = ResultsView(repository) if repository is not None else None

        tabs = QTabWidget()
        tabs.addTab(self.live_feed_view, "라이브 뷰")
        tabs.addTab(self.calibration_view, "캘리브레이션")
        tabs.addTab(self.camera_settings_view, "카메라 설정")
        tabs.addTab(self.stage1_view, "1단계: 렌즈 정렬")
        tabs.addTab(self.stage2_view, "2단계: 트래블 검사")
        if self.results_view is not None:
            tabs.addTab(self.results_view, "결과 조회")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(gate_layout)
        layout.addWidget(tabs)
        self.setCentralWidget(central)

        self.vm.frame_ready.connect(self.live_feed_view.on_frame)
        self.vm.frame_ready.connect(self.calibration_view.on_frame)
        self.vm.detection_ready.connect(self.live_feed_view.on_detection)
        self.vm.detection_ready.connect(self.stage1_view.on_detection)

    def _on_scope_id_changed(self, text: str) -> None:
        self.vm.set_scope_id(text)

    def _on_stage1_ready(self) -> None:
        pass  # 필요 시 2단계 탭으로 자동 전환 등의 UX를 여기에 추가
