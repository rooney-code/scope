"""그리드 캘리브레이션 화면: 자동/수동 원점 검출 + 화살표 미세조정.

작업자는 (1) '그리드 자동 검출'로 원점을 얻거나, 실패/불신뢰 시 '그리드 수동 검출'로 영상을
직접 클릭해 원점을 지정한 뒤, (2) 화살표 버튼으로 원점과 눈금 간격(스케일)을 화면을 보면서
미세 조정한다(빨간 좌표축/눈금이 실제 그리드와 맞는지 눈으로 확인 - LiveFeedView가 캘리브레이션
모드에서 그려줌). 자동/수동 검출 모두 원점만 잡을 뿐 스케일은 대략적인 시작 추정치라, 눈금
간격 미세조정 버튼을 한 번이라도 눌러야 확정된다(PixelAngleCalibration.is_ready 참고) -
과거에는 tick을 클릭하고 값을 입력하는 "클릭 스냅"으로 스케일을 잡았으나, 그 폼의 값/축
입력이 헷갈린다는 피드백으로 없앴다(사용자 요청, 2026-09-14).

영상 자체는 이 위젯이 그리지 않는다 - 화면 좌측에 항상 떠 있는 공용 LiveFeedView가
캘리브레이션 모드로 전환되어 보여준다(원점이 있으면 "시험 진행"과 동일하게 원점 기준으로
크롭/확대, 원점이 없거나 지정 대기 중이면 원본 전체 - LiveFeedView._is_cropped_view 참고).
클릭 위치는 LiveFeedView.frame_clicked_px 시그널을 통해 on_frame_clicked()로 전달된다
(MainWindow가 연결). 이 위젯은 컨트롤 패널(버튼)만 담당한다.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.calibration.grid_auto_detector import GridAutoDetector
from core.calibration.pixel_angle_calibration import PixelAngleCalibration


class CalibrationView(QWidget):
    # 원점 지정 대기 상태(그리드 수동 검출 armed) 변화를 LiveFeedView에 알려, 그 동안은
    # 크롭하지 않고 원본 전체를 보여주게 한다 - 이미 크롭된 화면 안에서는 새로 지정할
    # 원점(크로스헤어)이 화면 밖에 있을 수 있기 때문(MainWindow가 연결, 2026-09-14).
    origin_picking_changed = Signal(bool)

    def __init__(self, calibration: PixelAngleCalibration, camera_id: str, parent=None) -> None:
        super().__init__(parent)
        self.calibration = calibration
        self.camera_id = camera_id
        self.detector = GridAutoDetector()

        self._last_frame: np.ndarray | None = None
        # True인 동안은 다음 영상 클릭을 "이 위치를 원점으로 지정"으로 처리한다 - 자동
        # 검출이 실패하거나 믿을 수 없을 때 작업자가 직접 대략적인 원점을 클릭으로 잡고
        # 화살표로 미세조정하는 워크플로(사용자 요청, 2026-09-14).
        self._manual_origin_armed = False

        self.status_label = QLabel(
            "자동 검출을 실행하거나 이전 캘리브레이션을 불러오세요. "
            "(조리개를 최대로 열어 밝은 화면에서 촬영한 그리드 이미지를 권장)"
        )

        auto_btn = QPushButton("그리드 자동 검출")
        auto_btn.clicked.connect(self._on_auto_detect)

        manual_btn = QPushButton("그리드 수동 검출 (영상 클릭으로 원점 지정)")
        manual_btn.clicked.connect(self._on_manual_detect_armed)
        self._manual_btn = manual_btn

        # 화살표로 되돌리기엔 너무 멀리 잘못 잡은 원점을 처음부터 다시 잡고 싶을 때 쓰는
        # 초기화 버튼(사용자 요청, 2026-09-14) - 캘리브레이션을 완전히 지워서 원점 없는
        # 상태로 되돌린다(크롭도 자동으로 풀림, LiveFeedView._is_cropped_view 참고).
        clear_btn = QPushButton("원점 삭제 (캘리브레이션 초기화)")
        clear_btn.clicked.connect(self._on_clear_origin)

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

        # 눈금 간격(px-per-MOA) 미세조정: 큰 폭(대략치를 빠르게 맞춤) + 미세(정밀 조정) 두
        # 단계로 나눈다 - 클릭 스냅을 없애면서 이 버튼들이 스케일을 정하는 유일한 수단이
        # 됐는데, 시작 추정치(_DEFAULT_PX_PER_MOA_GUESS=10.0)와 실제값의 차이가 커서 미세
        # 단위(0.01)만 있으면 수십~수백 번 눌러야 했다(2026-09-14).
        SCALE_NUDGE_STEP_COARSE = 0.5
        SCALE_NUDGE_STEP_FINE = 0.01
        scale_group = QGroupBox(
            f"눈금 간격(px/MOA) 미세조정 - 큰 폭({SCALE_NUDGE_STEP_COARSE}) / 미세({SCALE_NUDGE_STEP_FINE})"
        )
        x_minus_c, x_plus_c = QPushButton("X −−"), QPushButton("X ++")
        y_minus_c, y_plus_c = QPushButton("Y −−"), QPushButton("Y ++")
        x_minus_f, x_plus_f = QPushButton("X −"), QPushButton("X +")
        y_minus_f, y_plus_f = QPushButton("Y −"), QPushButton("Y +")
        x_minus_c.clicked.connect(lambda: self._on_nudge_scale(-SCALE_NUDGE_STEP_COARSE, 0))
        x_plus_c.clicked.connect(lambda: self._on_nudge_scale(SCALE_NUDGE_STEP_COARSE, 0))
        y_minus_c.clicked.connect(lambda: self._on_nudge_scale(0, -SCALE_NUDGE_STEP_COARSE))
        y_plus_c.clicked.connect(lambda: self._on_nudge_scale(0, SCALE_NUDGE_STEP_COARSE))
        x_minus_f.clicked.connect(lambda: self._on_nudge_scale(-SCALE_NUDGE_STEP_FINE, 0))
        x_plus_f.clicked.connect(lambda: self._on_nudge_scale(SCALE_NUDGE_STEP_FINE, 0))
        y_minus_f.clicked.connect(lambda: self._on_nudge_scale(0, -SCALE_NUDGE_STEP_FINE))
        y_plus_f.clicked.connect(lambda: self._on_nudge_scale(0, SCALE_NUDGE_STEP_FINE))
        scale_layout = QGridLayout(scale_group)
        scale_layout.addWidget(x_minus_c, 0, 0)
        scale_layout.addWidget(x_minus_f, 0, 1)
        scale_layout.addWidget(x_plus_f, 0, 2)
        scale_layout.addWidget(x_plus_c, 0, 3)
        scale_layout.addWidget(y_minus_c, 1, 0)
        scale_layout.addWidget(y_minus_f, 1, 1)
        scale_layout.addWidget(y_plus_f, 1, 2)
        scale_layout.addWidget(y_plus_c, 1, 3)

        save_btn = QPushButton("저장")
        save_btn.clicked.connect(self._on_save)
        load_btn = QPushButton("불러오기")
        load_btn.clicked.connect(self._on_load)

        layout = QVBoxLayout(self)
        layout.addWidget(auto_btn)
        layout.addWidget(manual_btn)
        layout.addWidget(clear_btn)
        layout.addWidget(nudge_group)
        layout.addWidget(scale_group)
        layout.addLayout(self._hbox(save_btn, load_btn))
        layout.addWidget(self.status_label)
        layout.addStretch(1)

    @staticmethod
    def _hbox(*widgets) -> QHBoxLayout:
        box = QHBoxLayout()
        for w in widgets:
            box.addWidget(w)
        return box

    # ---- 프레임 캐시 (자동 검출용 - 화면 표시는 공용 LiveFeedView가 담당) ----
    def on_frame(self, frame_bgr: np.ndarray) -> None:
        self._last_frame = frame_bgr

    # ---- 액션 ----
    def _on_auto_detect(self) -> None:
        if self._last_frame is None:
            self.status_label.setText("프레임이 없습니다. 카메라가 실행 중인지 확인하세요.")
            return
        result = self.detector.detect(self._last_frame)
        if not result.found:
            self.status_label.setText(
                "자동 검출 실패 - 십자선을 못 찾았습니다. 조리개를 최대로 열어 밝은 화면에서 다시 "
                "시도하거나, 그래도 안 되면 '그리드 수동 검출'로 직접 클릭해 지정하세요."
            )
            return
        self.calibration.seed_from_auto_detection(self.camera_id, result)
        self.status_label.setText(
            f"자동 검출 완료: 원점={result.origin_px}. ⚠ 눈금 간격(스케일)은 아직 대략적인 추정치입니다 - "
            "영상의 빨간 좌표축이 실제 그리드와 맞는지 보면서 아래 '눈금 간격 미세조정' 버튼으로 X/Y "
            "양쪽 축을 맞추세요."
        )

    def _on_clear_origin(self) -> None:
        """화살표로 되돌리기엔 너무 멀리 잘못 잡은 원점을 처음부터 다시 잡고 싶을 때 - 캘리브레이션을
        완전히 지워 원점 없는 상태(원본 전체 화면)로 되돌린다."""
        self.calibration.clear()
        if self._manual_origin_armed:
            self._manual_origin_armed = False
            self.origin_picking_changed.emit(False)
        self.status_label.setText("원점을 삭제했습니다 - 자동 검출을 다시 실행하거나 수동으로 지정하세요.")

    def _on_manual_detect_armed(self) -> None:
        """자동 검출이 실패하거나 믿을 수 없을 때, 다음 영상 클릭을 원점 지정으로 처리하도록
        대기 상태로 전환한다. 이미 (어쩌면 잘못된) 원점 기준으로 크롭돼 있을 수 있으므로,
        새 원점을 어디든 클릭할 수 있게 크롭을 잠시 끈다(origin_picking_changed)."""
        self._manual_origin_armed = True
        self.origin_picking_changed.emit(True)
        self.status_label.setText("영상에서 원점으로 사용할 위치를 클릭하세요.")

    def on_frame_clicked(self, x: float, y: float) -> None:
        """공용 LiveFeedView가 캘리브레이션 모드에서 클릭된 원본 프레임 좌표를 알려줄 때 호출.

        그리드 수동 검출이 대기 상태(armed)일 때만 의미가 있다 - 그 상태가 아닌 클릭은
        무시한다(과거 클릭 스냅의 tick 위치 기억 용도였으나 그 기능 자체를 없앴다)."""
        if not self._manual_origin_armed:
            return
        self._manual_origin_armed = False
        self.origin_picking_changed.emit(False)
        self.calibration.seed_from_manual_origin(self.camera_id, (x, y))
        self.status_label.setText(
            f"수동 원점 지정 완료: ({x:.1f}, {y:.1f}). 화살표로 원점을 미세조정하고, 눈금 간격도 "
            "실제 그리드에 맞게 조정하세요."
        )

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
        ready_note = "" if self.calibration.is_ready else " (⚠ 아직 한쪽 축은 미확정)"
        self.status_label.setText(
            f"눈금 간격 조정: px_per_moa=({p.px_per_moa_x:.3f}, {p.px_per_moa_y:.3f}){ready_note}"
        )

    def _on_save(self) -> None:
        if self.calibration.profile is None:
            self.status_label.setText("저장할 캘리브레이션이 없습니다.")
            return
        self.calibration.save("calibration_profiles")
        self.status_label.setText("저장 완료.")

    def _on_load(self) -> None:
        self.load_saved()

    def load_saved(self) -> bool:
        """저장된 캘리브레이션이 있으면 불러온다. "불러오기" 버튼과, 프로그램 시작 시
        MainWindow가 자동으로 한 번 호출하는 경로가 이 메서드를 공유한다(사용자 요청:
        저장된 원점이 있으면 구동 시 자동으로 불러와달라, 2026-09-14). 반환값은 MainWindow가
        자동 호출 시 조용히 넘어갈지 판단하는 데 쓸 수 있도록 성공 여부를 알려준다."""
        if self.calibration.load("calibration_profiles", self.camera_id):
            if self.calibration.is_ready:
                self.status_label.setText("불러오기 완료.")
            else:
                # 과거(scale_confirmed 필드 도입 전) 저장 파일이거나, 스케일 확정 전에
                # 저장된 프로파일일 수 있다 - 그대로 두면 시험 진행 탭에서 조용히 잘못된
                # 값이 나올 수 있으므로 명시적으로 경고한다(2026-09-14).
                self.status_label.setText(
                    "불러오기 완료 - 단, ⚠ 이 프로파일은 스케일이 미확정입니다. 눈금 간격 미세조정으로 "
                    "X/Y 양쪽 축을 확정하세요."
                )
            return True

        self.status_label.setText("저장된 캘리브레이션이 없습니다 - 자동 검출부터 진행하세요.")
        return False
