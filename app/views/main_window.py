"""앱 셸: 좌측에 항상 떠 있는 공용 영상(LiveFeedView) + 우측 탭(시험 진행/캘리브레이션/
카메라 설정/결과 조회).

좌측 영상 영역을 최대한 키우고(카메라 원본은 정사각이 아니지만, 원점이 캘리브레이션된
뒤에는 원점 기준 크롭 결과가 거의 정사각이 되도록 설계됨 - LiveFeedView._relayout_square_image
참고), 카메라 설정/캘리브레이션은 프로그램 구동 시 한 번 맞춰두고 시험 중에는 거의 건드릴
일이 없어 가장 자주 쓰는 "시험 진행"을 기본 탭으로 둔다. 캘리브레이션 탭이 활성화되면
좌측 영상이 캘리브레이션 모드로 전환되는데, "시험 진행"과 마찬가지로 원점이 있으면 그
주변을 확대해서 보여준다(원점 지정 대기 중이거나 원점이 아예 없을 때만 원본 전체를
보여줌 - LiveFeedView._is_cropped_view 참고, 2026-09-14).
"""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from app.views.calibration_view import CalibrationView
from app.views.camera_settings_view import CameraSettingsView
from app.views.live_feed_view import LiveFeedView
from app.views.results_view import ResultsView
from app.views.stage1_alignment_view import Stage1AlignmentView
from app.views.stage2_travel_test_view import Stage2TravelTestView
from app.views.video_simulation_view import VideoSimulationView


class MainWindow(QMainWindow):
    def __init__(self, viewmodel: InspectionViewModel, camera_id: str, repository=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("조준경 불량 검사 프로그램")
        self.vm = viewmodel

        self.live_feed_view = LiveFeedView()
        self.live_feed_view.set_calibration(self.vm.calibration)
        self.calibration_view = CalibrationView(self.vm.calibration, camera_id)
        # 이 카메라(camera_id)로 저장해둔 캘리브레이션이 있으면 조용히 불러온다 - 매번
        # "캘리브레이션" 탭에서 "불러오기"를 눌러야 했던 불편함에 대한 개선(사용자 요청,
        # 2026-09-14). 없으면 load_saved()가 기본 안내 메시지를 그대로 둔다.
        self.calibration_view.load_saved()
        self.camera_settings_view = CameraSettingsView(self.vm.camera, self.vm.settings.camera)
        self.stage1_view = Stage1AlignmentView(self.vm.settings.stage1, on_ready_to_proceed=self._on_stage1_ready)
        self.stage1_view.set_calibration(self.vm.calibration)
        self.stage2_view = Stage2TravelTestView(self.vm)
        self.video_simulation_view = VideoSimulationView(self.vm)
        self.results_view = ResultsView(repository) if repository is not None else None

        # "시험 진행" 탭: 부품 ID + 1단계 + 2단계를 한 곳에 (가장 자주 쓰는 화면이라 기본 탭)
        self.scope_id_input = QLineEdit()
        self.scope_id_input.setPlaceholderText("예: SC-2026-04812 - 미입력 시 검사 시작 불가")
        self.scope_id_input.textChanged.connect(self._on_scope_id_changed)
        id_row = QHBoxLayout()
        id_row.addWidget(QLabel("부품 ID:"))
        id_row.addWidget(self.scope_id_input)

        # 확대/오버레이 컨트롤(원래 영상 아래에 있었음)을 시험 진행 탭 맨 위로 옮겨 영상이
        # 세로로 더 커질 수 있게 한다(사용자 피드백, 2026-09-14) - 구분선으로 컨트롤
        # 영역과 시험 내용을 구분.
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        video_separator = QFrame()
        video_separator.setFrameShape(QFrame.HLine)
        video_separator.setFrameShadow(QFrame.Sunken)

        test_tab = QWidget()
        test_layout = QVBoxLayout(test_tab)
        test_layout.addWidget(self.live_feed_view.controls_widget)
        test_layout.addWidget(separator)
        test_layout.addLayout(id_row)
        test_layout.addWidget(self.stage1_view)
        test_layout.addWidget(self.stage2_view)
        test_layout.addWidget(video_separator)
        test_layout.addWidget(self.video_simulation_view)
        # 남는 세로 공간을 탭 맨 아래로만 모아 위쪽 내용(1/2단계, 종합 결과, 버튼들)이
        # 빈 공간 없이 위로 붙어 보이게 한다(사용자 요청, 2026-09-15).
        test_layout.addStretch(1)

        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(test_tab, "시험 진행")
        self.right_tabs.addTab(self.calibration_view, "캘리브레이션")
        self.right_tabs.addTab(self.camera_settings_view, "카메라 설정")
        if self.results_view is not None:
            self.right_tabs.addTab(self.results_view, "결과 조회")
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)

        # 영상이 세로 높이에 맞춰 정사각으로 고정되므로(LiveFeedView._relayout_square_image),
        # 우측 패널을 좁게 잡으면 영상 좌우로 놀고 있는 여백이 큼 - 그 여백을 우측 패널에
        # 더 줘서(종합결과 표 등이 잘리지 않게) 활용한다. maximumWidth만 두면 QHBoxLayout이
        # stretch=1인 영상 쪽에 남는 공간을 전부 몰아줘서 우측 패널이 실제로는 커지지
        # 않으므로, setFixedWidth로 확실히 이 폭을 차지하게 한다(영상은 폭이 아니라 높이가
        # 기준이라 우측 패널을 넓혀도 영상이 작아지지 않음).
        self.right_tabs.setFixedWidth(700)
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
        self.calibration_view.origin_picking_changed.connect(self.live_feed_view.set_calibration_origin_picking)

    def _on_scope_id_changed(self, text: str) -> None:
        self.vm.set_scope_id(text)

    def _on_stage1_ready(self) -> None:
        pass  # 필요 시 2단계로 포커스 이동 등의 UX를 여기에 추가

    def _on_right_tab_changed(self, index: int) -> None:
        is_calibration = self.right_tabs.widget(index) is self.calibration_view
        self.live_feed_view.set_calibration_mode(is_calibration)
