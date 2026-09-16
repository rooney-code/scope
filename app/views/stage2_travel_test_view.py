"""2단계: 트래블 검사 실행 화면.

"시험 시작"을 누르면(부품 ID 입력 필요) 대기 baseline 추적이 시작된다 - 이후 레드닷을 실제로
상/하/좌/우 중 한 방향으로 일정량 이상 이동시키면 버튼을 누르지 않아도 자동으로 그 방향
시험이 시작된다(TravelTestStateMachine._feed_idle 참고, 사용자 요청 2026-09-16 - 매 방향마다
버튼을 누르는 번거로움을 줄이기 위함). 상/하/좌/우 버튼은 캘리브레이션 미세조정 화살표처럼
십자로 배치되어 있고, 자동 인식이 느리거나 애매할 때 수동으로 (재)시작하는 용도로 남아있다.
중앙의 "원점 복귀"도 마찬가지로 자동 판정(근접 범위+안정성)의 수동 오버라이드다.

데드클릭은 시험 중 바로 누르면 흐름이 끊기므로, 방향이 끝난 뒤 하단 종합표에서 O/X 토글로
표시한다(기본 X) - 작업자가 네 방향을 다 마친 뒤 기억을 더듬어 한 번에 표시할 수 있다.

안내 메시지는 "지금 뭘 해야 하는지"를 한 줄로만 보여주면 눈에 잘 안 띄고 이전 단계가 뭐였는지
알 수 없다는 지적(2026-09-16)에 따라, 지나간 메시지는 "완료" 표시로 남겨두는 로그 형태로
보여준다(guidance_log, QListWidget) - 새 메시지가 생기면 이전 메시지를 완료 처리하고 새로
추가한다.

"대기" 단계(원점 정렬/백래쉬 측정)는 3초(StabilitySettings.min_stable_duration_s) 동안
가만히 있어야 다음으로 넘어가는데, 그 대기시간이 화면에 안 보인다는 지적(2026-09-16)에 따라
"...... 3/2/1" 카운트다운을 붙인다 - 단, 매초 새 로그 항목을 만들면 로그가 초 단위로 도배되니
_set_guidance_stage()/_update_current_guidance_text()로 같은 항목의 텍스트만 갱신하고, 실제로
다른 단계로 넘어갈 때만 _set_guidance()로 새 항목을 만든다.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.viewmodels.inspection_viewmodel import ExcelSaveFailedError
from core.inspection.models import CheckType, DirectionTestResult, Stage2LiveState, TravelDirection, Verdict
from core.inspection.travel_test_state_machine import Phase


_DIRECTION_LABELS = {
    TravelDirection.UP: "상",
    TravelDirection.DOWN: "하",
    TravelDirection.LEFT: "좌",
    TravelDirection.RIGHT: "우",
}
_DIRECTION_ORDER = (TravelDirection.UP, TravelDirection.DOWN, TravelDirection.LEFT, TravelDirection.RIGHT)
# 종합 결과표 맨 위에 "시작점" 행을 별도로 둔다(사용자 요청, 2026-09-16) - CheckType이
# 아니라 DirectionTestResult.start_point_moa를 그대로 보여주는 가상의 행이라, CheckType과
# 겹치지 않는 문자열 상수를 행 종류로 쓴다.
_START_POINT_ROW = "start_point"
_ROW_LABELS = {
    _START_POINT_ROW: "시작점",
    CheckType.TRAVEL_AMOUNT: "이동량 (MOA)",
    CheckType.DEAD_CLICK: "데드클릭",
    CheckType.DRIFT: "드리프트 (MOA)",
    CheckType.SHIFT: "쉬프트 (MOA)",
    CheckType.BACKLASH: "백래쉬 (MOA)",
}
_ROW_ORDER = (
    _START_POINT_ROW,
    CheckType.TRAVEL_AMOUNT,
    CheckType.DEAD_CLICK,
    CheckType.DRIFT,
    CheckType.SHIFT,
    CheckType.BACKLASH,
)
# "백래쉬 측정을 완료 하였습니다" 메시지를 다음 안내로 넘어가기 전에 붙잡아두는 시간(초) -
# _refresh_guidance() 참고 (사용자 지적, 2026-09-16).
_BACKLASH_DONE_HOLD_S = 2.0
# 카운트다운 메시지("기본 문구 ...... N")의 구분자 - _set_guidance_stage()가 붙이고,
# _strip_countdown_suffix()가 "완료" 처리 시 떼어낸다.
_COUNTDOWN_SEP = " ...... "
# 시험 시작/방향/원점복귀 버튼들의 통일된 너비 - 제각각이면 줄이 안 맞아 보인다는
# 지적(2026-09-16)에 따라 고정폭으로 맞춘다.
_BUTTON_WIDTH_PX = 150
# "부품 ID:"/"시험 진행:" 라벨들의 통일된 너비 - 그 옆의 입력란/버튼 줄이 서로 세로로
# 맞춰 보이도록 한다(사용자 요청, 2026-09-16).
_ROW_LABEL_WIDTH_PX = 80


def _strip_countdown_suffix(text: str) -> str:
    """카운트다운 접미사(" ...... N")가 붙어있으면 떼어내고 기본 문구만 반환한다 - 카운트다운
    도중(예: "...... 1")에 다음 단계로 넘어가면서 "완료" 처리될 때, 남은 숫자가 안 지워진 채
    "...... 1  ······  완료"처럼 보이는 문제를 막는다(사용자 지적, 2026-09-16)."""
    return text.split(_COUNTDOWN_SEP, 1)[0]


# 방향이 끝났을 때(_on_direction_completed) 잠깐 붙잡아두는(_BACKLASH_DONE_HOLD_S) 완료 안내
# 문구를 실제로 어떤 검사까지 진행됐는지에 맞게 고른다 - 예전엔 항상 "백래쉬 측정을 완료
# 하였습니다"로 고정돼 있었는데, 34MOA쯤에서 '이동 완료'를 눌러 이동량 부족으로 바로 불량
# 종료된 경우처럼 백래쉬를 측정한 적도 없는데 측정했다고 안내하는 문제가 있었다(사용자 지적,
# 2026-09-16). 예전엔 CheckResult가 실패 즉시 중단됐지만(마지막 항목=실패 사유), 이제
# 이동량/쉬프트/드리프트는 하나가 불합격이어도 나머지까지 다 기록하므로(TravelTestStateMachine.
# mark_far_point_reached, 사용자 요청 2026-09-16 - "하나의 불량이 발생하면 나머지 항목은
# 기록하지 않는" 문제 개선) 마지막 항목이 실패 사유라는 보장이 없다 - 실패한 항목을 전부
# 찾아서 나열한다.
_CHECK_FAIL_LABELS = {
    CheckType.TRAVEL_AMOUNT: "이동량 부족",
    CheckType.SHIFT: "쉬프트 초과",
    CheckType.DRIFT: "드리프트 초과",
    CheckType.BACKLASH: "백래쉬 초과",
}


def _direction_completion_message(result: DirectionTestResult) -> str:
    if any(c.check_type == CheckType.BACKLASH for c in result.check_results):
        return "백래쉬 측정을 완료 하였습니다."
    failed_checks = [c for c in result.check_results if c.status == Verdict.FAIL]
    if failed_checks:
        reasons = ", ".join(_CHECK_FAIL_LABELS.get(c.check_type, c.check_type.value) for c in failed_checks)
        return f"{reasons}(으)로 판정되어 이 방향 시험이 조기 종료되었습니다. (백래쉬는 원점 복귀를 안 해 측정되지 않았습니다)"
    return "측정을 완료 하였습니다."


class Stage2TravelTestView(QWidget):
    def __init__(self, viewmodel, parent=None) -> None:
        super().__init__(parent)
        self.vm = viewmodel
        self._session_started = False
        self._current_guidance_text: str | None = None
        # 카운트다운 표시 중인 "단계"를 추적 - 같은 단계(같은 base_text) 안에서는 숫자만
        # 바뀌므로 새 로그 항목을 만들지 않는다(_set_guidance_stage 참고).
        self._current_guidance_stage: str | None = None
        # 지금 표시 중인 메시지가 실제로 카운트다운을 보여준 적이 있는지 - 카운트다운이
        # 없었던(한 번도 숫자를 보여준 적 없는) 메시지까지 "완료" 표시가 붙는 건 어색하다는
        # 지적(2026-09-16: "시프트 초과로 판정되어..." 같은 메시지에 "...... 완료"가 붙음)에
        # 따라, "완료" 표시는 실제로 카운트다운했던 메시지에만 붙인다.
        self._current_guidance_had_countdown: bool = False
        # "백래쉬 측정을 완료 하였습니다" 메시지를 다음 안내로 즉시 덮어쓰지 않고 잠깐
        # 붙잡아두기 위한 타임스탬프(_refresh_guidance 참고, 사용자 지적, 2026-09-16: 완료
        # 메시지가 다음 메시지와 동시에 "완료" 처리되어 읽을 새가 없었음).
        self._direction_completed_at: float | None = None
        # _direction_completed_at 동안 보여줄 문구 - 실제로 어디까지 측정됐는지에 따라
        # 달라진다(_direction_completion_message 참고).
        self._pending_completion_message: str | None = None
        # 엑셀 저장이 실패해 "시험 종료" 버튼을 다시 눌렀을 때 finalize_inspection() 대신
        # retry_excel_save()를 호출해야 함을 표시(_on_finalize 참고, 사용자 요청, 2026-09-16).
        self._excel_save_pending: bool = False

        # 안내 메시지 로그 - 눈에 잘 안 띄고 지난 메시지를 알 수 없다는 지적(2026-09-16)에
        # 따라, 큰 글씨 + 배경색으로 눈에 띄게 하고 지나간 메시지는 "완료" 표시로 남긴다.
        # 긴 문장이 가로 스크롤을 만들지 않도록 줄바꿈하고(사용자 지적, 2026-09-16), 줄바꿈된
        # 메시지도 잘 보이게 세로 길이를 넉넉히 잡는다(기존의 약 2배).
        self.guidance_log = QListWidget()
        self.guidance_log.setWordWrap(True)
        self.guidance_log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.guidance_log.setStyleSheet(
            "QListWidget { font-size: 12pt; background: #fffceb; border: 2px solid #d4a017; "
            "border-radius: 4px; padding: 4px; }"
            "QListWidget::item { padding: 3px 2px; }"
        )
        self.guidance_log.setMaximumHeight(300)

        # 부품 ID 입력 + 시험 시작/재시작을 한 줄에 - 입력란 옆에 바로 시작 버튼이 있어야
        # 자연스럽다는 요청(2026-09-16). 입력란이 너무 길다는 지적(2026-09-16)에 따라
        # 적당한 고정 폭으로 줄인다 - 버튼 정렬을 위해 그리드 열을 공유하던 예전 방식은
        # 버튼들이 이제 별도 행(cross)으로 옮겨가면서 더 이상 필요 없다.
        self.scope_id_input = QLineEdit()
        self.scope_id_input.setPlaceholderText("부품 ID")
        self.scope_id_input.setFixedWidth(300)
        self.scope_id_input.textChanged.connect(self.vm.set_scope_id)
        # 방향 버튼들과 너비를 맞춰 정렬이 흐트러지지 않게 한다(사용자 요청, 2026-09-16 -
        # "각 버튼의 너비는 250px로 고정").
        self.session_toggle_btn = QPushButton("시험 시작")
        self.session_toggle_btn.setFixedWidth(_BUTTON_WIDTH_PX)
        self.session_toggle_btn.clicked.connect(self._on_session_toggle)

        id_label = QLabel("부품 ID:")
        id_label.setFixedWidth(_ROW_LABEL_WIDTH_PX)
        session_row = QHBoxLayout()
        session_row.addWidget(id_label)
        session_row.addWidget(self.scope_id_input)
        session_row.addWidget(self.session_toggle_btn)
        session_row.addStretch(1)

        id_test_separator = QFrame()
        id_test_separator.setFrameShape(QFrame.HLine)
        id_test_separator.setFrameShadow(QFrame.Sunken)

        # 십자 배치(상/하/좌/우 + 가운데 원점 복귀) - 캘리브레이션 미세조정 화살표와 같은
        # 배치(사용자 요청, 2026-09-16). 각 방향 버튼은 자동 인식의 수동 오버라이드. 버튼마다
        # 있던 "대기중/이동중/복귀중/합격/불량" 상태 라벨은 안내 메시지 로그 + 아래 종합
        # 결과표와 내용이 겹쳐서 제거했다(사용자 지적, 2026-09-16).
        self._direction_buttons: dict[TravelDirection, QPushButton] = {}

        cross = QGridLayout()
        cross.addWidget(self._make_direction_cell(TravelDirection.UP), 0, 1)
        cross.addWidget(self._make_direction_cell(TravelDirection.LEFT), 1, 0)
        # 중앙 버튼은 단계에 따라 역할이 바뀐다 - OUTBOUND 중엔 "이동 완료"(기계적 한계로
        # 목표(35MOA-여유)에 살짝 못 미쳐 자동 판정이 안 걸리는 경우의 수동 확정,
        # mark_far_point_reached()), RETURN 중엔 "원점 복귀"(mark_returned_to_origin()) -
        # 둘 다 "자동 판정이 기본, 애매하면 수동 오버라이드" 패턴(사용자 지적, 2026-09-16:
        # 이동 완료 쪽 수동 버튼이 새 UI에 빠져 있어서 34.49MOA에서 멈춘 시험이 영영
        # RETURN으로 못 넘어간 문제).
        self._center_action_btn = QPushButton("원점 복귀")
        self._center_action_btn.setEnabled(False)
        self._center_action_btn.setFixedWidth(_BUTTON_WIDTH_PX)
        self._center_action_btn.clicked.connect(self._on_center_action)
        cross.addWidget(self._center_action_btn, 1, 1)
        cross.addWidget(self._make_direction_cell(TravelDirection.RIGHT), 1, 2)
        cross.addWidget(self._make_direction_cell(TravelDirection.DOWN), 2, 1)

        # "부품 ID:" 라벨 바로 아랫단에 "시험 진행:" 라벨을 두고 그 옆에 십자 버튼을 배치한다
        # (사용자 요청, 2026-09-16).
        test_progress_label = QLabel("시험 진행:")
        test_progress_label.setFixedWidth(_ROW_LABEL_WIDTH_PX)
        test_progress_row = QHBoxLayout()
        test_progress_row.addWidget(test_progress_label)
        test_progress_row.addLayout(cross)
        test_progress_row.addStretch(1)

        result_separator = QFrame()
        result_separator.setFrameShape(QFrame.HLine)
        result_separator.setFrameShadow(QFrame.Sunken)

        end_separator = QFrame()
        end_separator.setFrameShape(QFrame.HLine)
        end_separator.setFrameShadow(QFrame.Sunken)

        # 실시간 표시 줄 - 방향별 박스 버튼이 더 이상 필수 조작이 아니므로, 지금 무슨 일이
        # 일어나고 있는지(시작위치/현재위치/최대도달/최대편차) 화면으로 볼 수 있어야 한다는
        # 요청(2026-09-16)에 따라 추가.
        self._live_direction_label = QLabel()
        self._live_position_label = QLabel()
        self._live_extent_label = QLabel()
        self._reset_live_readout()
        live_box = QVBoxLayout()
        live_box.addWidget(self._live_direction_label)
        live_box.addWidget(self._live_position_label)
        live_box.addWidget(self._live_extent_label)

        # 종합 결과: 항목(체크타입 + 시작점) x 방향 + 결과. 값과 함께 그 값을 산출한 실제
        # 좌표도 같이 보여줘 더 자세히 감사할 수 있게 한다(사용자 요청, 2026-09-16 - 예:
        # "35.5(0, 35.5)").
        self.results_table = QTableWidget(len(_ROW_ORDER), len(_DIRECTION_ORDER) + 2)
        self.results_table.setHorizontalHeaderLabels(
            ["항목", *[_DIRECTION_LABELS[d] for d in _DIRECTION_ORDER], "결과"]
        )
        for row, row_kind in enumerate(_ROW_ORDER):
            self.results_table.setItem(row, 0, QTableWidgetItem(_ROW_LABELS[row_kind]))
        # 표가 전시 영역 폭을 꽉 채우도록 "항목" 열만 고정폭으로 두고 나머지(상/하/좌/우/결과)
        # 는 균등하게 늘어나게 한다(사용자 요청, 2026-09-16 - "표의 너비를 넓혀서 전시
        # 영역을 꽉채워주고").
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.results_table.setColumnWidth(0, 140)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 행이 하나 늘면서(시작점 행 추가) 기본 크기로는 세로 스크롤바가 생겼다는 지적
        # (2026-09-16) - 헤더 높이 + 행 높이 x 행 개수만큼 최소 높이를 직접 잡아 모든 행이
        # 스크롤 없이 한 번에 보이게 한다.
        row_height = self.results_table.verticalHeader().defaultSectionSize()
        table_height = self.results_table.horizontalHeader().height() + row_height * len(_ROW_ORDER) + 4
        self.results_table.setMinimumHeight(table_height)
        self._refresh_results_table()

        self.abort_btn = QPushButton("시험 중지")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self._on_abort)

        self.finalize_btn = QPushButton("시험 종료 (결과 저장)")
        self.finalize_btn.setEnabled(False)
        self.finalize_btn.clicked.connect(self._on_finalize)

        layout = QVBoxLayout(self)
        layout.addWidget(self.guidance_log)
        layout.addLayout(session_row)
        layout.addWidget(id_test_separator)
        layout.addLayout(test_progress_row)
        layout.addLayout(live_box)
        layout.addWidget(result_separator)
        layout.addWidget(QLabel("종합 결과:"))
        layout.addWidget(self.results_table)
        layout.addWidget(end_separator)
        layout.addWidget(self.abort_btn)
        layout.addWidget(self.finalize_btn)

        self.vm.phase_changed.connect(self._on_phase_changed)
        self.vm.direction_completed.connect(self._on_direction_completed)
        self.vm.inspection_completed.connect(self._on_inspection_completed)
        self.vm.inspection_finalized.connect(self._on_inspection_finalized)
        self.vm.stage2_live_update.connect(self._on_live_update)

        self._refresh_guidance()

    # ---- 위젯 생성 ----
    def _make_direction_cell(self, direction: TravelDirection) -> QPushButton:
        btn = QPushButton(f"{_DIRECTION_LABELS[direction]} 시작")
        btn.setFixedWidth(_BUTTON_WIDTH_PX)
        btn.clicked.connect(lambda _checked=False, d=direction: self._on_start_direction(d))
        self._direction_buttons[direction] = btn
        return btn

    def _reset_live_readout(self) -> None:
        self._live_direction_label.setText("진행 중인 방향: -")
        self._live_position_label.setText("시작 위치: -    현재 위치: -")
        self._live_extent_label.setText("최대 도달: -    최대 편차: -")

    # ---- 액션 ----
    def _on_session_toggle(self) -> None:
        if not self._session_started:
            if not self.vm.can_start_inspection():
                self._set_guidance("부품 ID를 먼저 입력하세요 - 시험을 시작할 수 없습니다.")
                return
            self.vm.start_inspection_session()
            self._session_started = True
            self.session_toggle_btn.setText("전체 재시작")
            self._refresh_guidance()
        else:
            self._on_restart_all()

    def _on_start_direction(self, direction: TravelDirection) -> None:
        if not self.vm.can_start_inspection():
            self._set_guidance("부품 ID를 먼저 입력하세요 - 시험을 시작할 수 없습니다.")
            return
        try:
            self.vm.start_direction(direction)
        except RuntimeError as exc:
            self._set_guidance(str(exc))

    def _on_center_action(self) -> None:
        phase = self.vm.state_machine.phase
        if phase == Phase.OUTBOUND:
            self.vm.mark_far_point_reached()
        elif phase == Phase.RETURN:
            self.vm.mark_returned_to_origin()

    def _on_abort(self) -> None:
        self.vm.abort_current_direction()
        self._reset_live_readout()
        # 중지 전에 이미 완료된 방향이 있었다면(예: 상 합격 후 하 진행 중 중지) 그 결과만으로도
        # 시험을 종료할 수 있어야 한다(사용자 요청, 2026-09-16).
        if self.vm.is_ready_to_finalize:
            self.finalize_btn.setEnabled(True)
        self._refresh_guidance()

    def _on_restart_all(self) -> None:
        self.vm.restart_all()
        self._session_started = False
        self.session_toggle_btn.setText("시험 시작")
        for direction in _DIRECTION_ORDER:
            self._direction_buttons[direction].setText(f"{_DIRECTION_LABELS[direction]} 시작")
        self._center_action_btn.setText("원점 복귀")
        self._center_action_btn.setEnabled(False)
        self._reset_live_readout()
        self._refresh_results_table()
        self.finalize_btn.setEnabled(False)
        self.guidance_log.clear()
        self._current_guidance_text = None
        self._current_guidance_stage = None
        self._current_guidance_had_countdown = False
        self._direction_completed_at = None
        self._pending_completion_message = None
        self._excel_save_pending = False
        self._refresh_guidance()

    def _on_finalize(self) -> None:
        # 이전 시도가 엑셀 저장 실패로 멈춰 있었으면(_excel_save_pending), 상태기계를 다시
        # finalize()하면 예외가 나므로(이미 finalized) 저장만 재시도한다(사용자 요청,
        # 2026-09-16 - 엑셀 파일이 열려 있어 저장 실패해도 프로그램이 죽거나 결과가 유실되지
        # 않고, 닫은 뒤 같은 버튼으로 다시 시도할 수 있어야 함).
        if self._excel_save_pending:
            self._retry_excel_save()
            return
        try:
            overall = self.vm.finalize_inspection()
        except ExcelSaveFailedError as exc:
            self._excel_save_pending = True
            self._on_excel_save_failed(str(exc))
            return
        self._on_finalize_succeeded(overall)

    def _retry_excel_save(self) -> None:
        try:
            overall = self.vm.retry_excel_save()
        except ExcelSaveFailedError as exc:
            self._on_excel_save_failed(str(exc))
            return
        self._excel_save_pending = False
        self._on_finalize_succeeded(overall)

    def _on_excel_save_failed(self, message: str) -> None:
        QMessageBox.warning(self, "결과 저장 실패", message)
        self._set_guidance("엑셀 파일을 닫은 뒤 '시험 종료' 버튼을 다시 눌러 저장을 재시도해주세요.")

    def _on_finalize_succeeded(self, overall: str) -> None:
        self._set_guidance(f"저장 완료 - 최종 판정: {overall}")
        self.finalize_btn.setEnabled(False)
        for btn in self._direction_buttons.values():
            btn.setEnabled(False)

    def _on_dead_click_toggled(self, direction: TravelDirection, checked: bool) -> None:
        try:
            self.vm.set_dead_click(direction, checked)
        except RuntimeError:
            pass  # 아직 완료된 적 없는 방향 - 체크박스가 비활성화돼 있어 정상적으론 안 일어남

    # ---- 뷰모델 시그널 반응 ----
    def _on_phase_changed(self, phase_name: str) -> None:
        current = self.vm.state_machine.current_direction
        # "시험 중지"는 실제로 진행 중인 방향이 있을 때만 의미가 있다 - 없는데 누르면
        # state_machine.abort_current_direction()이 예외를 던진다(사용자 지적, 2026-09-16:
        # "중지할 진행 중인 방향이 없습니다" 로그). OUTBOUND/RETURN일 때만 활성화한다.
        self.abort_btn.setEnabled(phase_name in (Phase.OUTBOUND.name, Phase.RETURN.name))
        if phase_name == Phase.OUTBOUND.name and current is not None:
            self._center_action_btn.setText("이동 완료")
            self._center_action_btn.setEnabled(True)
        elif phase_name == Phase.RETURN.name and current is not None:
            self._center_action_btn.setText("원점 복귀")
            self._center_action_btn.setEnabled(True)
        else:
            self._center_action_btn.setText("원점 복귀")
            self._center_action_btn.setEnabled(False)
        self._refresh_guidance()

    def _on_direction_completed(self, result: DirectionTestResult) -> None:
        self._sync_direction_status()
        self._reset_live_readout()
        self._refresh_results_table()
        if self.vm.is_ready_to_finalize:
            self.finalize_btn.setEnabled(True)
        # 방향이 막 끝난 시점을 기록해둔다 - _refresh_guidance()가 이 시각으로부터 일정
        # 시간 동안은 완료 안내 메시지를 붙잡아두고 다음 안내로 곧바로 덮어쓰지 않는다
        # (사용자 지적, 2026-09-16: 완료 메시지와 다음 메시지가 동시에 "완료" 처리되어
        # 버려서 읽을 새가 없었음). 실제로 어디까지 측정됐는지(백래쉬까지 갔는지, 아니면
        # 이동량 부족 등으로 도중에 불량 종료됐는지)에 따라 문구를 다르게 고른다(사용자
        # 지적, 2026-09-16: 34MOA에서 '이동 완료'를 눌러 백래쉬를 측정한 적도 없는데
        # "백래쉬 측정을 완료 하였습니다"라고 안내됨).
        message = _direction_completion_message(result)
        # 불량으로 끝난 방향은 "다음 방향으로"보다 "지금 저장할 수도 있다"는 선택지를 바로
        # 알려주는 게 낫다는 요청(2026-09-16) - 불량이 나면 계속 진행하기보다 그 자리에서
        # 끝내고 싶어하는 경우가 많다는 판단.
        if result.verdict == Verdict.FAIL and self.vm.is_ready_to_finalize:
            message += " 필요하면 '시험 종료' 버튼을 눌러 지금까지 결과를 저장할 수 있습니다."
        self._pending_completion_message = message
        self._direction_completed_at = time.time()
        self._refresh_guidance()

    def _on_inspection_completed(self, overall_verdict: str) -> None:
        if self.vm.is_ready_to_finalize:
            self.finalize_btn.setEnabled(True)
        self._refresh_guidance()

    def _on_inspection_finalized(self, overall_verdict: str) -> None:
        self.finalize_btn.setEnabled(False)

    def _on_live_update(self, state: Stage2LiveState) -> None:
        if state.current_direction is None:
            self._reset_live_readout()
            # 방향이 끝난 뒤 "원점으로 이동" -> "다음 방향 진행" 두 단계를 구분하려면 idle
            # 상태에서도 현재 위치(x/y)가 필요하다(사용자 요청, 2026-09-16).
            self._refresh_guidance(state)
            return
        self._live_direction_label.setText(f"진행 중인 방향: {_DIRECTION_LABELS[state.current_direction]}")
        start_text = "시작 위치: -"
        if state.baseline_moa is not None:
            bx, by = state.baseline_moa
            start_text = f"시작 위치: x={bx:.2f}, y={by:.2f}"
        current_text = "현재 위치: -"
        if state.current_x_moa is not None and state.current_y_moa is not None:
            current_text = f"현재 위치: x={state.current_x_moa:.2f}, y={state.current_y_moa:.2f}"
        self._live_position_label.setText(f"{start_text}    {current_text}")
        max_reached = f"{state.max_primary_reached_moa:.2f}" if state.max_primary_reached_moa is not None else "-"
        max_cross = f"{state.max_abs_cross_moa:.2f}" if state.max_abs_cross_moa is not None else "-"
        self._live_extent_label.setText(f"최대 도달: {max_reached}    최대 편차: {max_cross}")
        self._refresh_guidance(state)

    # ---- 안내 메시지 ----
    def _set_guidance(self, text: str) -> None:
        """안내 메시지를 로그에 추가한다 - 단순 setText 하나로는 눈에 잘 안 띄고 지난
        메시지를 알 수 없다는 지적(2026-09-16)에 따라, 이전 메시지는 "완료" 표시로 남기고
        새 메시지를 굵게 추가한다. 같은 텍스트가 연속되면(매 프레임 재계산되는 경우가 많음)
        중복 추가하지 않는다."""
        if text == self._current_guidance_text:
            return
        if self._current_guidance_text is not None:
            last_item = self.guidance_log.item(self.guidance_log.count() - 1)
            if last_item is not None:
                base_text = _strip_countdown_suffix(self._current_guidance_text)
                if self._current_guidance_had_countdown:
                    # 카운트다운 단계(_set_guidance_stage)가 끝나기 직전(예: "...... 1")에
                    # 다음 단계로 넘어오면, 남은 숫자가 안 지워진 채로 "완료"가 붙어
                    # "...... 1 ...... 완료"처럼 보이는 문제가 있었다(사용자 지적,
                    # 2026-09-16) - "완료"로 표시할 때는 카운트다운 접미사를 떼고 기본
                    # 문구만 남긴다.
                    last_item.setText(f"{base_text}  ······  완료")
                else:
                    # 카운트다운을 한 번도 보여준 적 없는 메시지(예: "쉬프트 초과로
                    # 판정되어...")에까지 "...... 완료"가 붙는 건 어색하다는 지적
                    # (2026-09-16)에 따라, 이런 메시지는 문구 그대로 흐리게만 표시한다.
                    last_item.setText(base_text)
                last_item.setForeground(QColor("#8a8a8a"))
                normal_font = QFont(last_item.font())
                normal_font.setBold(False)
                last_item.setFont(normal_font)
        new_item = QListWidgetItem(text)
        bold_font = QFont(new_item.font())
        bold_font.setBold(True)
        new_item.setFont(bold_font)
        new_item.setForeground(QColor("#1a1a1a"))
        self.guidance_log.addItem(new_item)
        self.guidance_log.scrollToBottom()
        self._current_guidance_text = text
        self._current_guidance_stage = None  # 일반 메시지는 카운트다운 단계 추적 대상이 아님
        self._current_guidance_had_countdown = False  # 새 메시지는 아직 카운트다운을 보여준 적 없음

    def _update_current_guidance_text(self, text: str) -> None:
        """지금 표시 중인(맨 아래) 안내 메시지의 텍스트만 바꾼다 - _set_guidance()처럼 새
        로그 항목을 만들거나 이전 항목을 "완료" 처리하지 않는다. 카운트다운처럼 같은 단계
        안에서 숫자만 바뀌는 경우 전용(_set_guidance_stage 참고) - 매초 새 항목을 만들면
        로그가 초 단위로 도배된다."""
        if text == self._current_guidance_text:
            return
        self._current_guidance_text = text
        last_item = self.guidance_log.item(self.guidance_log.count() - 1)
        if last_item is not None:
            last_item.setText(text)

    def _set_guidance_stage(self, base_text: str, remaining_s: float | None) -> None:
        """3초 대기(StabilitySettings.min_stable_duration_s) 같은 "단계"를 카운트다운과
        함께 보여준다 - 대기시간이 화면에 안 보인다는 지적(2026-09-16)에 따라 추가. 아직
        안정 추적이 시작 안 됐으면(remaining_s=None) 카운트다운 없이 기본 문구만, 추적
        중이면 "...... N"을 붙인다. 같은 단계(base_text) 안에서는 초마다 새 로그 항목을
        만들지 않고 텍스트만 갱신하고, 다른 단계로 막 넘어온 참이면 새 항목을 만든다."""
        text = base_text if remaining_s is None else f"{base_text}{_COUNTDOWN_SEP}{max(0, math.ceil(remaining_s))}"
        if self._current_guidance_stage == base_text and self._current_guidance_text is not None:
            self._update_current_guidance_text(text)
        else:
            self._set_guidance(text)
            self._current_guidance_stage = base_text
        # _set_guidance()가 방금 False로 리셋했을 수 있으므로, 실제로 카운트다운 숫자를
        # 보여준 적 있으면(remaining_s가 한 번이라도 온 적 있으면) 여기서 다시 세운다.
        if remaining_s is not None:
            self._current_guidance_had_countdown = True

    def _refresh_guidance(self, live_state: Stage2LiveState | None = None) -> None:
        """"지금 뭘 해야 하는지" 사용자-프로그램 간 약속이 애매하다는 지적(2026-09-16)에
        따라, 매 단계마다 다음 행동을 명확히 안내한다. _set_guidance() 하나로 통일해서
        예전처럼 여러 곳에서 서로 다른 문구를 개별적으로 setText하다 꼬이는 일이 없게 한다."""
        if not self._session_started:
            self._set_guidance("부품 ID를 입력하고 '시험 시작'을 눌러주세요")
            return

        sm = self.vm.state_machine
        direction = sm.current_direction
        stage2 = self.vm.settings.stage2

        if direction is None:
            if sm.phase == Phase.INSPECTION_DONE:
                self._set_guidance("모든 방향 완료 - '시험 종료'를 눌러 저장하세요")
                return

            # 백래쉬 측정이 막 끝난 직후엔, 다음 안내로 바로 넘어가기 전에 그 완료 메시지를
            # 잠깐 붙잡아둔다 - 곧장 다음 메시지로 덮어쓰면 로그에서 둘 다 동시에 "완료"
            # 처리되어 버려 사용자가 읽을 새가 없었다(사용자 지적, 2026-09-16).
            if self._direction_completed_at is not None:
                if time.time() - self._direction_completed_at < _BACKLASH_DONE_HOLD_S:
                    self._set_guidance(self._pending_completion_message or "측정을 완료 하였습니다.")
                    return
                self._direction_completed_at = None

            # "원점 대기 중" <-> "원점 정렬 완료"를 상태기계의 idle baseline 캡처 여부로
            # 판단한다(idle_baseline_captured) - 처음 시작할 때든 방향 하나가 끝난 뒤든 같은
            # 기준/문구를 쓴다. 예전엔 "레드닷을 원점에 두고 대기하세요 - 이동을 감지하면
            # 자동으로..."처럼 "대기"와 "이동"을 한 문장에 섞어 써서 모순처럼 읽힌다는 지적
            # (2026-09-16)이 있어, 정렬 대기 -> 정렬 완료를 명확히 분리된 두 단계로 안내한다.
            if sm.idle_baseline_captured:
                self._set_guidance("원점 정렬이 완료되었습니다 - 시험 방향으로 이동해주세요")
            else:
                self._set_guidance_stage(
                    "원점 정렬을 위해 원점으로 이동하여 대기해주세요", sm.idle_wait_remaining_s
                )
            return

        label = _DIRECTION_LABELS[direction]
        if sm.phase == Phase.OUTBOUND:
            # 목표(35MOA)에 도달한 뒤에도 바로 "측정 완료"로 넘어가지 않고, 실제로는
            # 안정성이 확인될 때까지(3초) 기다리는 구간이 있다 - 그 대기 자체가 안 보이면
            # "이동해주세요" 메시지와 "측정하였습니다" 메시지 사이가 비어 보인다는 지적
            # (2026-09-16)에 따라, 목표 도달 여부(target_reached)로 구간을 나눠 카운트다운을
            # 보여준다. mark_far_point_reached()가 도달 판정에 쓰는 것과 같은 여유값
            # (MEASUREMENT_EPSILON_MOA)을 그대로 써서 기준을 통일한다.
            target_reached = sm.max_primary_reached_moa >= stage2.travel_target_moa - sm.MEASUREMENT_EPSILON_MOA
            if target_reached:
                self._set_guidance_stage(
                    "최대 도달 거리를 측정 중입니다 - 이 위치에서 잠시 대기해주세요 (또는 '이동 완료' 클릭)",
                    sm.direction_wait_remaining_s,
                )
            else:
                self._set_guidance(
                    f"{label} 방향으로 이동 중입니다 - {stage2.travel_target_moa:.0f}MOA 이상 이동해주세요 "
                    "(기계적 한계로 더 못 가면 '이동 완료' 클릭)"
                )
        elif sm.phase == Phase.RETURN:
            last_primary = live_state.last_primary_moa if live_state is not None else None
            if last_primary is not None and abs(last_primary) <= stage2.near_zero_band_moa:
                self._set_guidance_stage(
                    "백래쉬 측정을 위해 복귀 포인트에서 잠시 대기해주세요 (또는 '원점 복귀' 클릭)",
                    sm.direction_wait_remaining_s,
                )
            else:
                self._set_guidance("최대 도달 거리를 측정하였습니다 - 원점 방향으로 복귀해주세요")

    # ---- 방향 버튼 라벨("시작"/"재시작") 동기화 (수동 버튼 클릭뿐 아니라 자동 인식/
    # 데드클릭 토글 후에도 정확해야 하므로, "방금 바뀐 방향 하나만"이 아니라 매번 전체를
    # 다시 읽어 반영한다) ----
    def _sync_direction_status(self) -> None:
        results_by_direction = {r.direction: r for r in self.vm.state_machine.direction_results}
        for direction in _DIRECTION_ORDER:
            if direction in results_by_direction:
                self._direction_buttons[direction].setText(f"{_DIRECTION_LABELS[direction]} 재시작")

    # ---- 종합 결과 표 ----
    def _refresh_results_table(self) -> None:
        results_by_direction = {r.direction: r for r in self.vm.state_machine.direction_results}

        for row, row_kind in enumerate(_ROW_ORDER):
            row_statuses = []
            for col, direction in enumerate(_DIRECTION_ORDER, start=1):
                result = results_by_direction.get(direction)
                if row_kind == _START_POINT_ROW:
                    # 시작점은 판정 대상이 아니라 감사용 참고 정보라 결과 열은 항상 "-"
                    # (사용자 요청, 2026-09-16).
                    item = QTableWidgetItem(self._start_point_text(result))
                    item.setTextAlignment(Qt.AlignCenter)
                    self.results_table.setItem(row, col, item)
                    continue
                if row_kind == CheckType.DEAD_CLICK:
                    status = self._set_dead_click_cell(row, col, direction, result)
                else:
                    text, status = self._cell_for(result, row_kind)
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignCenter)
                    if status == "불량":
                        item.setForeground(QColor("#c23c3c"))
                    self.results_table.setItem(row, col, item)
                if status is not None:
                    row_statuses.append(status)

            result_col = len(_DIRECTION_ORDER) + 1
            if row_kind == _START_POINT_ROW or not row_statuses:
                row_text = "-"
            elif "불량" in row_statuses:
                row_text = "불량"
            else:
                row_text = "합격"
            result_item = QTableWidgetItem(row_text)
            result_item.setTextAlignment(Qt.AlignCenter)
            if row_text == "불량":
                result_item.setForeground(QColor("#c23c3c"))
            self.results_table.setItem(row, result_col, result_item)

    @staticmethod
    def _start_point_text(result: DirectionTestResult | None) -> str:
        if result is None or result.start_point_moa is None:
            return "-"
        x, y = result.start_point_moa
        return f"({x:.1f}, {y:.1f})"

    def _set_dead_click_cell(
        self, row: int, col: int, direction: TravelDirection, result: DirectionTestResult | None
    ) -> str | None:
        """데드클릭 행은 텍스트 대신 O/X 토글 체크박스로 표시한다(기본 X) - 시험 중 바로
        누르면 흐름이 끊기므로, 방향이 끝난 뒤 결과표에서 한 번에 토글하기 위함(사용자 요청,
        2026-09-16)."""
        flagged = result is not None and any(c.check_type == CheckType.DEAD_CLICK for c in result.check_results)
        checkbox = QCheckBox()
        checkbox.setChecked(flagged)
        checkbox.setEnabled(result is not None)
        checkbox.toggled.connect(lambda checked, d=direction: self._on_dead_click_toggled(d, checked))

        container = QWidget()
        inner = QHBoxLayout(container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setAlignment(Qt.AlignCenter)
        inner.addWidget(checkbox)
        self.results_table.setCellWidget(row, col, container)

        if result is None:
            return None
        return "불량" if flagged else "합격"

    @staticmethod
    def _cell_for(result: DirectionTestResult | None, check_type: CheckType) -> tuple[str, str | None]:
        """표의 한 칸(방향 x 항목) 표시 문자열과 판정("합격"/"불량"/None=미평가)."""
        if result is None:
            return "-", None

        check = next((c for c in result.check_results if c.check_type == check_type), None)
        if check is None:
            return "-", None  # 이동량/쉬프트/드리프트 불합격으로 원점 복귀 없이 종료돼 백래쉬만 없는 경우 등
        if check.measured_value is None:
            value_text = "-"
        elif check.point_moa is not None:
            # 측정값과 함께 그 값을 산출한 실제 좌표도 보여준다(사용자 요청, 2026-09-16 -
            # 예: "35.5(0, 35.5)").
            px, py = check.point_moa
            value_text = f"{check.measured_value:.1f}({px:.1f}, {py:.1f})"
        else:
            value_text = f"{check.measured_value:.1f}"
        return value_text, check.status.value
