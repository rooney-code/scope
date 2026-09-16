"""캘리브레이션 상태 경고 - 원점을 못 찾았거나 스케일이 아직 확정 안 됐으면 시험 진행
탭에서 눈에 띄게 경고한다.

예전엔 여기서 "레드닷을 원점으로 이동하세요 / 정렬 완료 - 2단계 준비됨" 같은 정렬 안내와
오차 수치도 보여줬지만, Stage2TravelTestView의 통합 안내 메시지 로그가 그 역할을 대신하게
되면서 중복이라 제거했다(사용자 요청, 2026-09-16) - 여기 남은 건 캘리브레이션 자체가
안 됐을 때의 경고뿐이다.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

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
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._calibration = None  # 외부에서 주입 (main_window에서 연결)

        # 캘리브레이션이 안 됐거나 미완성인 채로 시험을 진행하면 화면은 정상처럼 보이는데
        # 실제로는 터무니없는 오차 숫자가 나오는 문제가 있었다(실측으로 확인, 2026-09-14) -
        # 원점을 못 찾았거나 스케일이 아직 확정 안 됐으면 눈에 띄게 경고하고 다음에 뭘 해야
        # 하는지 안내한다.
        self.calibration_warning_label = QLabel(_NO_CALIBRATION_MSG)
        self.calibration_warning_label.setWordWrap(True)
        self.calibration_warning_label.setStyleSheet("color: #b02a2a; font-weight: bold;")

        layout = QVBoxLayout(self)
        layout.addWidget(self.calibration_warning_label)

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
