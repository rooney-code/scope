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

from PySide6.QtWidgets import QFrame, QHBoxLayout, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from app.views.calibration_view import CalibrationView
from app.views.camera_settings_view import CameraSettingsView
from app.views.inspection_settings_view import InspectionSettingsView
from app.views.live_feed_view import LiveFeedView
from app.views.results_view import ResultsView
from app.views.simulation_view import SimulationView
from app.views.stage1_alignment_view import Stage1AlignmentView
from app.views.stage2_travel_test_view import Stage2TravelTestView
from app.views.video_simulation_view import VideoSimulationView
from core.calibration.grid_auto_detector import GridDetectionResult
from core.camera.mock_camera_service import MockCameraService


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

        # 확대/오버레이 컨트롤을 "시험 진행" 탭에서 "캘리브레이션" 탭 맨 위로 옮긴다 - 확대는
        # 원점을 클릭할 때(캘리브레이션 중)나 쓰고, 오버레이 표시는 시험 진행 중에 바꿀 일이
        # 없다는 판단(사용자 요청, 2026-09-16). LiveFeedView.controls_widget은 애초에 밖에서
        # 원하는 곳에 배치하도록 설계돼 있다(live_feed_view.py 참고).
        controls_separator = QFrame()
        controls_separator.setFrameShape(QFrame.HLine)
        controls_separator.setFrameShadow(QFrame.Sunken)
        calibration_layout = self.calibration_view.layout()
        calibration_layout.insertWidget(0, self.live_feed_view.controls_widget)
        calibration_layout.insertWidget(1, controls_separator)
        self.camera_settings_view = CameraSettingsView(self.vm.camera, self.vm.settings.camera)

        # "검사 설정" 탭: 트래블 시험 판정값(목표 이동량/각종 임계값)과 정지 판정 대기시간을
        # settings.json을 직접 열어 고치는 대신 프로그램 안에서 바꿀 수 있게 한다 - 카메라
        # 설정과 같은 자리(탭)에 둔다(사용자 요청, 2026-09-16: "설정값은 사용자가 바꿔야
        # 하는 부분이니 이 프로그램으로 해결하면 좋을것 같아").
        self.inspection_settings_view = InspectionSettingsView(self.vm.settings)

        # "시뮬레이션" 탭:가진 시험 영상만으로는 모든 케이스를 재현하기 힘들다는 요청
        # (2026-09-16)에 따라, 실제 카메라와 완전히 독립된 MockCameraService(검은 배경 +
        # 가상 레드닷)와 그 전용 InspectionViewModel을 둔다. settings는 실제 시험과 같은
        # 객체를 공유해서(같은 임계값/절차) 시뮬레이션에서 확인한 절차가 실제 탭과 동일하게
        # 동작함을 보장하되, 세션 상태(부품 ID/진행 중인 방향/결과)는 별도 상태기계라 서로
        # 영향을 주지 않는다.
        self.sim_camera = MockCameraService(render_style="simple")
        self.sim_camera.open()
        self.sim_vm = InspectionViewModel(self.sim_camera, self.vm.settings)
        self.sim_vm.calibration.seed_from_auto_detection(
            "SIMULATION", GridDetectionResult(found=True, origin_px=self.sim_camera.origin_px())
        )
        self.sim_vm.calibration.profile.px_per_moa_x = self.sim_camera.px_per_moa()
        self.sim_vm.calibration.profile.px_per_moa_y = self.sim_camera.px_per_moa()
        self.sim_vm.calibration.profile.scale_confirmed_x = True
        self.sim_vm.calibration.profile.scale_confirmed_y = True
        self.simulation_view = SimulationView(self.sim_vm, self.sim_camera)

        self.stage1_view = Stage1AlignmentView()
        self.stage1_view.set_calibration(self.vm.calibration)
        self.stage2_view = Stage2TravelTestView(self.vm)
        self.video_simulation_view = VideoSimulationView(self.vm)
        self.results_view = ResultsView(repository) if repository is not None else None

        # "시험 진행" 탭: 1단계(캘리브레이션 경고) + 2단계(부품 ID 입력 포함)를 한 곳에
        # (가장 자주 쓰는 화면이라 기본 탭). 부품 ID 입력은 "시험 시작" 버튼과 한 화면
        # (Stage2TravelTestView)에 있는 게 자연스러워 그쪽으로 옮겼다(사용자 요청,
        # 2026-09-16 - 입력란 옆에 바로 시작 버튼을 두기 위함).
        video_separator = QFrame()
        video_separator.setFrameShape(QFrame.HLine)
        video_separator.setFrameShadow(QFrame.Sunken)

        test_tab = QWidget()
        test_layout = QVBoxLayout(test_tab)
        test_layout.addWidget(self.stage1_view)
        test_layout.addWidget(self.stage2_view)
        # 영상 재생 컨트롤이 2단계 화면 바로 아래 붙어 답답해 보인다는 지적(2026-09-16)에
        # 따라 약간 띄운다.
        test_layout.addSpacing(20)
        test_layout.addWidget(video_separator)
        test_layout.addWidget(self.video_simulation_view)
        # 남는 세로 공간을 탭 맨 아래로만 모아 위쪽 내용(1/2단계, 종합 결과, 버튼들)이
        # 빈 공간 없이 위로 붙어 보이게 한다(사용자 요청, 2026-09-15).
        test_layout.addStretch(1)

        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(test_tab, "시험 진행")
        self.right_tabs.addTab(self.calibration_view, "캘리브레이션")
        self.right_tabs.addTab(self.camera_settings_view, "카메라 설정")
        self.right_tabs.addTab(self.inspection_settings_view, "검사 설정")
        self.right_tabs.addTab(self.simulation_view, "시뮬레이션")
        if self.results_view is not None:
            self.right_tabs.addTab(self.results_view, "결과 조회")
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)

        # 영상이 실제로 표시되는 최대 크기는 LiveFeedView._relayout_square_image의 960px
        # 캡이 정한다 - live_feed_view 위젯 자체를 stretch=1로 두면 그보다 훨씬 넓은
        # 컨테이너 안에서 영상이 가운데 작게 떠 좌우로 큰 공백이 생긴다(사용자 지적,
        # 2026-09-16). 위젯 자체의 최대 폭을 그 캡에 맞춰 제한해서 컨테이너가 영상 크기로
        # 줄어들게 하고, 남는 공간은 전부 우측 패널(종합결과 표 등)에 준다.
        self.live_feed_view.setMaximumWidth(1000)
        self.right_tabs.setFixedWidth(900)
        body = QHBoxLayout()
        body.addWidget(self.live_feed_view, stretch=0)
        body.addWidget(self.right_tabs, stretch=1)

        central = QWidget()
        central.setLayout(body)
        self.setCentralWidget(central)

        self.vm.frame_ready.connect(self.live_feed_view.on_frame)
        self.vm.frame_ready.connect(self.calibration_view.on_frame)
        self.vm.detection_ready.connect(self.live_feed_view.on_detection)
        self.vm.detection_ready.connect(self.stage1_view.on_detection)
        self.vm.fps_stats_updated.connect(self.live_feed_view.on_fps_stats)
        self.live_feed_view.frame_clicked_px.connect(self.calibration_view.on_frame_clicked)
        self.calibration_view.origin_picking_changed.connect(self.live_feed_view.set_calibration_origin_picking)

    def _on_right_tab_changed(self, index: int) -> None:
        current_widget = self.right_tabs.widget(index)
        is_calibration = current_widget is self.calibration_view
        self.live_feed_view.set_calibration_mode(is_calibration)
        # 시뮬레이션 탭을 보고 있을 때만 그 안의 주기적 스트리밍을 켠다 - 안 보이는 동안도
        # 계속 돌면 실제 카메라 파이프라인과 별개로 불필요하게 CPU를 쓴다(사용자 지적,
        # 2026-09-16). 시작 탭은 "시험 진행"이라 초기 상태(꺼짐)와 자연히 일치한다.
        self.simulation_view.set_streaming_active(current_widget is self.simulation_view)
