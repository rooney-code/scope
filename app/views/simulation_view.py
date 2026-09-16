"""시뮬레이션 탭: 실제 시험 데이터(영상)가 없어도 전체 절차(자동 방향 인식, 안내 메시지,
결과 판정, 데드클릭 토글, 텍스트 저장까지)를 검증할 수 있게 해주는 화면.

가진 시험 영상만으로는 모든 케이스(예: 특정 방향으로 정확히 35MOA 이상 이동하는 경우, 데드클릭
등)를 재현하기 어렵다는 요청(2026-09-16)에 따라, 화살표 버튼으로 0.1MOA씩 가상 레드닷을
움직여 실제 카메라와 완전히 동일한 파이프라인(RedDotDetector -> BlobTracker ->
TravelTestStateMachine)을 통과시킨다. 실제 카메라의 InspectionViewModel과는 완전히 독립된
별도의 MockCameraService + InspectionViewModel을 쓰므로 동시에 켜져 있어도 서로 영향이 없다.

배경 이미지는 필요 없다는 요청에 따라 MockCameraService를 render_style="simple"(검은 배경 +
레드닷만)로 생성한다. 원점/좌표축 오버레이는 LiveFeedView가 대신 그려준다.

시험 진행 탭과 동일하게 Stage1AlignmentView + Stage2TravelTestView를 그대로 재사용한다 - 단,
영상 선택/재생 파트(VideoSimulationView)는 여기서는 의미가 없으므로(재생할 영상 자체가 없음)
빼고 화살표 조작 패널로 대체한다.

키보드 방향키도 마우스 버튼과 동일하게 동작한다(사용자 요청, 2026-09-16) - QShortcut을
Qt.WidgetWithChildrenShortcut 컨텍스트로 이 위젯에 걸어서, 이 탭 안의 어떤 자식 위젯에
포커스가 있어도(부품 ID 입력란만 빼고 - QLineEdit이 방향키를 커서 이동으로 직접 소비하므로
자연스럽게 셔틀컷보다 우선함) 눌리게 한다. 버튼/키 둘 다 누르고 있으면 연속 입력되도록
autoRepeat을 켠다(사용자 요청, 2026-09-16) - 버튼은 QPushButton.setAutoRepeat, 키보드는
QShortcut의 기본 autoRepeat(True)을 그대로 쓴다.

버튼을 누를 때만 프레임을 보내면(클릭 이벤트 기반) 가만히 있는 동안은 프레임이 전혀 전송되지
않는다 - 그런데 자동 방향 인식(TravelTestStateMachine._feed_idle)과 원점 복귀 자동 판정은
"최근 N개 샘플이 실제 경과 시간(min_stable_duration_s, 기본 3초) 동안 안정적이었는지"를
보는 방식이라, 실시간 스트림처럼 계속 프레임이 들어와야 동작한다. 클릭 기반으로는 원점에
가만히 있는 상태 자체가 감지되지 않아 그 다음 이동도 인식되지 않는 문제가 있었다(사용자
확인, 2026-09-16: 원점에 두고 있다가 상단으로 이동해도 안내 메시지가 안 바뀜). 그래서
QTimer로 실제 카메라처럼 주기적으로(150ms) 현재 위치의 프레임을 계속 흘려보낸다 - 버튼을
누르지 않고 가만히 있어도 "대기 안정성"이 실제 시간 기준으로 쌓이도록 한다.

또한 시뮬레이션 중 레드닷 좌표/상태기계가 인식한 방향·판정을 콘솔에서 볼 수 있어야 한다는
요청(2026-09-16)에 따라 "디버그 로그 출력" 체크박스를 둔다 - InspectionViewModel의 기존
[stage2]/[detect] 로그(DetectionSettings.debug_logging)를 그대로 켠다(video_simulation_view.py
와 동일한 패턴). 실제 카메라와 settings를 공유하므로 이 체크박스는 실제 시험 진행 탭의 로그
출력에도 함께 영향을 준다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from app.views.live_feed_view import LiveFeedView
from app.views.stage1_alignment_view import Stage1AlignmentView
from app.views.stage2_travel_test_view import Stage2TravelTestView
from core.camera.mock_camera_service import MockCameraService

_NUDGE_STEP_MOA = 0.1
_STREAM_INTERVAL_MS = 150  # 실제 카메라처럼 가만히 있어도 계속 프레임을 흘려보내는 주기


class SimulationView(QWidget):
    def __init__(self, viewmodel: InspectionViewModel, camera: MockCameraService, parent=None) -> None:
        super().__init__(parent)
        self.vm = viewmodel
        self.camera = camera
        self._x_moa = 0.0
        self._y_moa = 0.0

        self.live_feed_view = LiveFeedView()
        self.live_feed_view.set_calibration(self.vm.calibration)
        # 탭 하나 안에 영상+조작 패널+시험 진행 화면을 전부 담아야 하므로, 실제 카메라
        # 영역(좌측 상시 패널)만큼 크게 둘 필요는 없다 - 적당한 크기로 제한.
        self.live_feed_view.setMaximumWidth(480)
        self.vm.frame_ready.connect(self.live_feed_view.on_frame)
        self.vm.detection_ready.connect(self.live_feed_view.on_detection)
        self.vm.fps_stats_updated.connect(self.live_feed_view.on_fps_stats)

        # 캘리브레이션 미세조정 화살표와 같은 패턴(사용자 요청, 2026-09-16) - 위/아래/좌/우
        # 버튼을 누를 때마다 가상 레드닷이 0.1MOA씩 이동하고, 그 결과 프레임을 실제 카메라와
        # 동일한 파이프라인(_on_frame)에 동기적으로 밀어넣는다.
        nudge_group = QGroupBox(f"가상 레드닷 이동 ({_NUDGE_STEP_MOA}MOA, 누르고 있으면 연속 이동/방향키 가능)")
        up_btn = QPushButton("▲")
        down_btn = QPushButton("▼")
        left_btn = QPushButton("◀")
        right_btn = QPushButton("▶")
        # 누르고 있으면 계속 이동해야 한다는 요청(2026-09-16) - QPushButton의 내장
        # autoRepeat을 쓴다(누른 채 delay만큼 있으면 interval 간격으로 clicked가 반복 emit됨).
        for btn in (up_btn, down_btn, left_btn, right_btn):
            btn.setAutoRepeat(True)
            btn.setAutoRepeatDelay(300)
            btn.setAutoRepeatInterval(60)
        up_btn.clicked.connect(lambda: self._on_nudge(0.0, _NUDGE_STEP_MOA))
        down_btn.clicked.connect(lambda: self._on_nudge(0.0, -_NUDGE_STEP_MOA))
        left_btn.clicked.connect(lambda: self._on_nudge(-_NUDGE_STEP_MOA, 0.0))
        right_btn.clicked.connect(lambda: self._on_nudge(_NUDGE_STEP_MOA, 0.0))
        # 캘리브레이션/시험 진행의 십자 배치와 같은 패턴으로, 가운데에 "원점 복귀"를 둔다
        # (사용자 요청, 2026-09-16) - 가상 좌표를 (0,0)으로 즉시 되돌린다.
        origin_btn = QPushButton("원점\n복귀")
        origin_btn.clicked.connect(self._on_origin_reset)
        nudge_layout = QGridLayout(nudge_group)
        nudge_layout.addWidget(up_btn, 0, 1)
        nudge_layout.addWidget(left_btn, 1, 0)
        nudge_layout.addWidget(origin_btn, 1, 1)
        nudge_layout.addWidget(right_btn, 1, 2)
        nudge_layout.addWidget(down_btn, 2, 1)

        # 키보드 방향키 연동(사용자 요청, 2026-09-16) - QShortcut의 기본 컨텍스트는
        # Qt.WindowShortcut(창 전체에서 항상 활성)이라 다른 탭을 보고 있어도 눌리는 문제가
        # 있으므로, 이 탭(과 그 자식 위젯 중 하나)이 포커스를 가지고 있을 때만 활성화되는
        # Qt.WidgetWithChildrenShortcut으로 좁힌다. QShortcut은 기본적으로 autoRepeat=True라
        # 키를 누르고 있으면 버튼과 마찬가지로 계속 반복 입력된다.
        for key, dx, dy in (
            (Qt.Key_Up, 0.0, _NUDGE_STEP_MOA),
            (Qt.Key_Down, 0.0, -_NUDGE_STEP_MOA),
            (Qt.Key_Left, -_NUDGE_STEP_MOA, 0.0),
            (Qt.Key_Right, _NUDGE_STEP_MOA, 0.0),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda dx=dx, dy=dy: self._on_nudge(dx, dy))

        # video_simulation_view.py의 "검출 로그 출력" 체크박스와 동일한 패턴 - 실제
        # 시험(settings 공유)과 로그 출력 여부를 함께 켜고 끈다.
        self._debug_log_checkbox = QCheckBox("디버그 로그 출력 (레드닷 좌표 + 인식 상태)")
        self._debug_log_checkbox.setChecked(self.vm.settings.detection.debug_logging)
        self._debug_log_checkbox.toggled.connect(self._on_debug_log_toggled)

        left_col = QVBoxLayout()
        left_col.addWidget(self.live_feed_view, stretch=1)
        left_col.addWidget(nudge_group)
        left_col.addWidget(self._debug_log_checkbox)

        # 시험 진행 탭과 동일한 화면(Stage1 정렬 경고 + Stage2 십자 조작/안내 메시지/결과표)을
        # 그대로 재사용 - 영상 재생 파트만 빠진다(재생할 실제 영상이 없으므로).
        self.stage1_view = Stage1AlignmentView()
        self.stage1_view.set_calibration(self.vm.calibration)
        self.vm.detection_ready.connect(self.stage1_view.on_detection)
        self.stage2_view = Stage2TravelTestView(self.vm)

        right_col = QVBoxLayout()
        right_col.addWidget(self.stage1_view)
        right_col.addWidget(self.stage2_view)
        right_col.addStretch(1)

        layout = QHBoxLayout(self)
        layout.addLayout(left_col)
        layout.addLayout(right_col, stretch=1)

        # 실제 카메라 스레드처럼, 조작이 없어도 현재 위치의 프레임을 계속 흘려보낸다 - 그래야
        # 원점 대기/원점 복귀 같은 "실제 경과 시간 동안 가만히 있었는지" 판정이 동작한다(위
        # 클래스 docstring 참고). 단, 이 탭을 보고 있을 때만 돈다(set_streaming_active) -
        # MainWindow가 항상 이 위젯을 미리 만들어두므로, 무조건 켜두면 사용자가 이 탭을 한
        # 번도 안 열어도 실제 카메라 파이프라인과는 별개로 검출+렌더링이 백그라운드에서
        # 계속 돌아 프로그램 전체가 불필요하게 느려지는 문제가 있었다(사용자 지적,
        # 2026-09-16 - "속도 저하를 일으킬만한 불필요한 작업" 점검 중 발견).
        self._timer = QTimer(self)
        self._timer.setInterval(_STREAM_INTERVAL_MS)
        self._timer.timeout.connect(self._push_frame)
        self._push_frame()  # 시작하자마자 원점(0,0)의 레드닷이 보이도록 초기 프레임 한 장 표시

    def set_streaming_active(self, active: bool) -> None:
        """시뮬레이션 탭이 실제로 보일 때만 주기적 스트리밍을 켠다(MainWindow가 탭 전환
        시 호출) - 꺼져 있는 동안은 화살표 조작 시(클릭할 때마다 _push_frame이 직접
        호출됨)에만 프레임이 흐르고, 대기 안정성 카운트다운 등 시간 기반 판정은 이 탭을
        보고 있을 때만 진행된다(사용자 요청에 따른 트레이드오프 - 안 보이는 탭까지 실시간
        판정을 계속 돌릴 이유가 없다는 판단)."""
        if active:
            self._timer.start()
        else:
            self._timer.stop()

    def _on_nudge(self, dx_moa: float, dy_moa: float) -> None:
        self._x_moa += dx_moa
        self._y_moa += dy_moa
        self._push_frame()

    def _on_origin_reset(self) -> None:
        self._x_moa = 0.0
        self._y_moa = 0.0
        self._push_frame()

    def _push_frame(self) -> None:
        self.camera.set_dot_position_moa(self._x_moa, self._y_moa)
        # 스레드 캡처(camera.start())를 쓰지 않고, 프레임 한 장을 즉시 동기적으로 렌더해
        # 파이프라인에 직접 주입한다(scripts/smoke_test_mock_ui.py와 동일한 방식) - 실제
        # 타이머(위 __init__)가 조작 여부와 무관하게 이 메서드를 주기적으로도 호출해 실시간
        # 스트림처럼 동작하게 한다.
        frame = self.camera._render_frame()  # noqa: SLF001 - 가상 카메라 전용 직접 호출
        self.vm._on_frame(frame)  # noqa: SLF001 - 백그라운드 스레드 없이 동기 처리

    def _on_debug_log_toggled(self, checked: bool) -> None:
        self.vm.settings.detection.debug_logging = checked
