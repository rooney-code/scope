"""앱 셸: 좌측에 항상 떠 있는 공용 영상(LiveFeedView) + 우측 탭(시험 진행/캘리브레이션/
카메라 설정/결과 조회).

영상이 원본 3088x2076로 정사각에 가까워 좌측 영상 영역을 최대한 키우고, 카메라 설정/
캘리브레이션은 프로그램 구동 시 한 번 맞춰두고 시험 중에는 거의 건드릴 일이 없어 가장 자주
쓰는 "시험 진행"을 기본 탭으로 둔다. 캘리브레이션 탭이 활성화되면 좌측 영상이 캘리브레이션
모드(크롭 없이 원본 전체 + 클릭으로 tick 스냅)로 전환된다.
"""
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

        self.live_feed_view = LiveFeedView()
        self.live_feed_view.set_calibration(self.vm.calibration)
        self.calibration_view = CalibrationView(self.vm.calibration, camera_id)
        self.camera_settings_view = CameraSettingsView(self.vm.camera, self.vm.settings.camera)
        self.stage1_view = Stage1AlignmentView(self.vm.settings.stage1, on_ready_to_proceed=self._on_stage1_ready)
        self.stage1_view.set_calibration(self.vm.calibration)
        self.stage2_view = Stage2TravelTestView(self.vm)
        self.results_view = ResultsView(repository) if repository is not None else None

        # "시험 진행" 탭: 부품 ID + 1단계 + 2단계를 한 곳에 (가장 자주 쓰는 화면이라 기본 탭)
        self.scope_id_input = QLineEdit()
        self.scope_id_input.setPlaceholderText("예: SC-2026-04812 - 미입력 시 검사 시작 불가")
        self.scope_id_input.textChanged.connect(self._on_scope_id_changed)
        id_row = QHBoxLayout()
        id_row.addWidget(QLabel("부품 ID:"))
        id_row.addWidget(self.scope_id_input)

        test_tab = QWidget()
        test_layout = QVBoxLayout(test_tab)
        test_layout.addLayout(id_row)
        test_layout.addWidget(self.stage1_view)
        test_layout.addWidget(self.stage2_view)

        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(test_tab, "시험 진행")
        self.right_tabs.addTab(self.calibration_view, "캘리브레이션")
        self.right_tabs.addTab(self.camera_settings_view, "카메라 설정")
        if self.results_view is not None:
            self.right_tabs.addTab(self.results_view, "결과 조회")
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)

        # 와이어프레임 비율 그대로: 우측 패널은 폭을 제한해두고 영상이 남는 공간을 전부
        # 차지하게 한다(영상이 거의 정사각이라 이렇게 해야 크게 보임).
        self.right_tabs.setMaximumWidth(420)
        body = QHBoxLayout()
        body.addWidget(self.live_feed_view, stretch=1)
        body.addWidget(self.right_tabs, stretch=0)

        central = QWidget()
        central.setLayout(body)
        self.setCentralWidget(central)

        self.vm.frame_ready.connect(self.live_feed_view.on_frame)
        self.vm.frame_ready.connect(self.calibration_view.on_frame)
        self.vm.detection_ready.connect(self.live_feed_view.on_detection)
        self.vm.detection_ready.connect(self.stage1_view.on_detection)
        self.live_feed_view.frame_clicked_px.connect(self.calibration_view.on_frame_clicked)

    def _on_scope_id_changed(self, text: str) -> None:
        self.vm.set_scope_id(text)

    def _on_stage1_ready(self) -> None:
        pass  # 필요 시 2단계로 포커스 이동 등의 UX를 여기에 추가

    def _on_right_tab_changed(self, index: int) -> None:
        is_calibration = self.right_tabs.widget(index) is self.calibration_view
        self.live_feed_view.set_calibration_mode(is_calibration)
