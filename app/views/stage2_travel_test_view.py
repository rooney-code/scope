"""2단계: 트래블 검사 실행 화면.

작업자가 누르는 버튼은 최소화한다 - 방향마다 자기 자신의 시작/재시작 버튼과 데드클릭
버튼만 있는 박스 하나씩(상/하/좌/우, 순서 무관하게 언제든 누를 수 있음), 목표(35MOA)/
원점 근처에서 잠깐 멈추면 이동량/쉬프트/드리프트/백래쉬가 전부 자동으로 평가된다(실제
작업자가 하던 방식: 이동 -> 눈금 확인(멈춤) -> 후진 -> 원점 근처 멈춤 을 그대로 인식).
동시에 두 방향을 진행할 수 없으므로, 다른 방향이 진행 중일 때 어떤 박스의 시작 버튼을
눌러도 그 진행 중이던 방향의 미완성 데이터는 폐기되고 새로 누른 방향이 시작된다(상태
기계가 처리) - 재시작 전 항상 원점으로 이동해야 하므로 부분 기록은 의미가 없다는 판단.
"""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.inspection.models import CheckType, DirectionTestResult, TravelDirection
from core.inspection.travel_test_state_machine import Phase


_DIRECTION_LABELS = {
    TravelDirection.UP: "상",
    TravelDirection.DOWN: "하",
    TravelDirection.LEFT: "좌",
    TravelDirection.RIGHT: "우",
}
_DIRECTION_ORDER = (TravelDirection.UP, TravelDirection.DOWN, TravelDirection.LEFT, TravelDirection.RIGHT)
_CHECK_ROW_LABELS = {
    CheckType.TRAVEL_AMOUNT: "이동량 (MOA)",
    CheckType.DEAD_CLICK: "데드클릭",
    CheckType.DRIFT: "드리프트 (MOA)",
    CheckType.SHIFT: "쉬프트 (MOA)",
    CheckType.BACKLASH: "백래쉬 (MOA)",
}
_CHECK_ROW_ORDER = (CheckType.TRAVEL_AMOUNT, CheckType.DEAD_CLICK, CheckType.DRIFT, CheckType.SHIFT, CheckType.BACKLASH)


class Stage2TravelTestView(QWidget):
    def __init__(self, viewmodel, parent=None) -> None:
        super().__init__(parent)
        self.vm = viewmodel

        self.status_label = QLabel("대기 중")

        # 방향별 박스 - 각자 자기 시작/재시작 버튼 + 데드클릭 버튼
        self._status_labels: dict[TravelDirection, QLabel] = {}
        self._start_buttons: dict[TravelDirection, QPushButton] = {}
        self._dead_click_buttons: dict[TravelDirection, QPushButton] = {}

        direction_boxes = QVBoxLayout()
        for direction in _DIRECTION_ORDER:
            box = QGroupBox(_DIRECTION_LABELS[direction])
            row = QHBoxLayout(box)

            status = QLabel("대기 중")
            self._status_labels[direction] = status

            start_btn = QPushButton("시작")
            start_btn.clicked.connect(lambda _checked=False, d=direction: self._on_start_direction(d))
            self._start_buttons[direction] = start_btn

            dead_click_btn = QPushButton("데드클릭")
            dead_click_btn.setEnabled(False)
            dead_click_btn.clicked.connect(self._on_dead_click)
            self._dead_click_buttons[direction] = dead_click_btn

            row.addWidget(status, stretch=1)
            row.addWidget(start_btn)
            row.addWidget(dead_click_btn)
            direction_boxes.addWidget(box)

        # 종합 결과: 항목(체크타입) x 방향 + 결과
        self.results_table = QTableWidget(len(_CHECK_ROW_ORDER), len(_DIRECTION_ORDER) + 2)
        self.results_table.setHorizontalHeaderLabels(
            ["항목", *[_DIRECTION_LABELS[d] for d in _DIRECTION_ORDER], "결과"]
        )
        for row, check_type in enumerate(_CHECK_ROW_ORDER):
            self.results_table.setItem(row, 0, QTableWidgetItem(_CHECK_ROW_LABELS[check_type]))
        # 우측 패널 폭이 좁아 6개 열을 억지로 다 채우면 라벨이 잘림 - 각 열에 읽기 편한
        # 최소 폭을 주고 안 맞으면 표 자체가 가로 스크롤되게 한다(내용을 잘라내지 않음).
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setMinimumSectionSize(60)
        self.results_table.setColumnWidth(0, 110)
        for col in range(1, len(_DIRECTION_ORDER) + 2):
            self.results_table.setColumnWidth(col, 55)
        self.results_table.verticalHeader().setVisible(False)
        self._refresh_results_table()

        abort_btn = QPushButton("시험 중지")
        abort_btn.clicked.connect(self._on_abort)
        restart_all_btn = QPushButton("전체 재시작")
        restart_all_btn.clicked.connect(self._on_restart_all)
        interrupt_layout = QHBoxLayout()
        interrupt_layout.addWidget(abort_btn)
        interrupt_layout.addWidget(restart_all_btn)

        self.finalize_btn = QPushButton("시험 종료 (결과 저장)")
        self.finalize_btn.setEnabled(False)
        self.finalize_btn.clicked.connect(self._on_finalize)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(direction_boxes)
        layout.addWidget(QLabel("종합 결과:"))
        layout.addWidget(self.results_table)
        layout.addLayout(interrupt_layout)
        layout.addWidget(self.finalize_btn)

        self.vm.phase_changed.connect(self._on_phase_changed)
        self.vm.direction_completed.connect(self._on_direction_completed)
        self.vm.inspection_completed.connect(self._on_inspection_completed)
        self.vm.inspection_finalized.connect(self._on_inspection_finalized)

    # ---- 액션 ----
    def _on_start_direction(self, direction: TravelDirection) -> None:
        if not self.vm.can_start_inspection():
            self.status_label.setText("부품 ID를 먼저 입력하세요 - 시험을 시작할 수 없습니다.")
            return
        try:
            self.vm.start_direction(direction)
        except RuntimeError as exc:
            self.status_label.setText(str(exc))
            return
        self._set_all_dead_click_enabled(False)
        self._dead_click_buttons[direction].setEnabled(True)
        for d, label in self._status_labels.items():
            if d == direction:
                label.setText("이동 중")
        self.status_label.setText(
            f"{_DIRECTION_LABELS[direction]} 방향 이동 중 - 목표(35MOA) 근처에서 잠깐 멈추면 자동 평가됩니다"
        )

    def _on_dead_click(self) -> None:
        self.vm.flag_dead_click()

    def _on_abort(self) -> None:
        self.vm.abort_current_direction()
        self._set_all_dead_click_enabled(False)
        self.status_label.setText("시험 중지됨 (미기록)")

    def _on_restart_all(self) -> None:
        self.vm.restart_all()
        for direction in _DIRECTION_ORDER:
            self._status_labels[direction].setText("대기 중")
            self._start_buttons[direction].setText("시작")
        self._set_all_dead_click_enabled(False)
        self._refresh_results_table()
        self.status_label.setText("전체 재시작됨")
        self.finalize_btn.setEnabled(False)

    def _on_finalize(self) -> None:
        overall = self.vm.finalize_inspection()
        self.status_label.setText(f"저장 완료 - 최종 판정: {overall}")
        self.finalize_btn.setEnabled(False)
        for btn in self._start_buttons.values():
            btn.setEnabled(False)

    def _set_all_dead_click_enabled(self, enabled: bool) -> None:
        for btn in self._dead_click_buttons.values():
            btn.setEnabled(enabled)

    # ---- 뷰모델 시그널 반응 ----
    def _on_phase_changed(self, phase_name: str) -> None:
        current = self.vm.state_machine.current_direction
        if phase_name == Phase.RETURN.name and current is not None:
            self._status_labels[current].setText("복귀 중")
            self.status_label.setText("원점으로 복귀 중 - 원점 근처에서 잠깐 멈추면 자동 평가됩니다")

    def _on_direction_completed(self, result: DirectionTestResult) -> None:
        self._status_labels[result.direction].setText(result.verdict.value)
        self._start_buttons[result.direction].setText("재시작")
        self._set_all_dead_click_enabled(False)
        self._refresh_results_table()
        if self.vm.is_ready_to_finalize:
            self.finalize_btn.setEnabled(True)

    def _on_inspection_completed(self, overall_verdict: str) -> None:
        self.status_label.setText(f"모든 방향 완료 - 전체 판정: {overall_verdict} (시험 종료를 눌러 저장하세요)")
        if self.vm.is_ready_to_finalize:
            self.finalize_btn.setEnabled(True)

    def _on_inspection_finalized(self, overall_verdict: str) -> None:
        self.finalize_btn.setEnabled(False)

    # ---- 종합 결과 표 ----
    def _refresh_results_table(self) -> None:
        results_by_direction = {r.direction: r for r in self.vm.state_machine.direction_results}

        for row, check_type in enumerate(_CHECK_ROW_ORDER):
            row_statuses = []
            for col, direction in enumerate(_DIRECTION_ORDER, start=1):
                text, status = self._cell_for(results_by_direction.get(direction), check_type)
                item = QTableWidgetItem(text)
                if status == "불량":
                    item.setForeground(QColor("#c23c3c"))
                self.results_table.setItem(row, col, item)
                if status is not None:
                    row_statuses.append(status)

            result_col = len(_DIRECTION_ORDER) + 1
            if not row_statuses:
                row_text = "-"
            elif "불량" in row_statuses:
                row_text = "불량"
            else:
                row_text = "합격"
            result_item = QTableWidgetItem(row_text)
            if row_text == "불량":
                result_item.setForeground(QColor("#c23c3c"))
            self.results_table.setItem(row, result_col, result_item)

    @staticmethod
    def _cell_for(result: DirectionTestResult | None, check_type: CheckType) -> tuple[str, str | None]:
        """표의 한 칸(방향 x 항목) 표시 문자열과 판정("합격"/"불량"/None=미평가)."""
        if result is None:
            return "-", None

        check = next((c for c in result.check_results if c.check_type == check_type), None)
        if check_type == CheckType.DEAD_CLICK:
            if check is not None:
                return "발생", "불량"
            return "X", "합격"

        if check is None:
            return "-", None  # 앞선 항목에서 이미 불량이라 이 항목까지 도달 못함
        value_text = f"{check.measured_value:.1f}" if check.measured_value is not None else "-"
        return value_text, check.status.value
