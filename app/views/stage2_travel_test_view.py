"""2단계: 트래블 검사 실행 화면.

작업자가 누르는 버튼은 최소화한다 - 방향당 "OO 시험 시작" 1번뿐이고, 목표(35MOA)/원점
근처에서 잠깐 멈추면 이동량/쉬프트/드리프트/백래쉬가 전부 자동으로 평가된다(실제 작업자가
하던 방식: 이동 -> 눈금 확인(멈춤) -> 후진 -> 원점 근처 멈춤 을 그대로 인식). 사람이 누르는
건 "인터럽트" 성격의 두 버튼(데드클릭 발생/시험 중지)과, 조작 실수가 의심될 때의 재시험,
그리고 전부 끝난 뒤 결과를 확정하는 시험 종료 뿐이다.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.inspection.models import DirectionTestResult, TravelDirection
from core.inspection.travel_test_state_machine import Phase


_DIRECTION_LABELS = {
    TravelDirection.UP: "상",
    TravelDirection.DOWN: "하",
    TravelDirection.LEFT: "좌",
    TravelDirection.RIGHT: "우",
}


class Stage2TravelTestView(QWidget):
    def __init__(self, viewmodel, parent=None) -> None:
        super().__init__(parent)
        self.vm = viewmodel

        # 방향 선택 (기본: 상/하/좌/우 전부 체크, 순서는 체크된 순서)
        select_group = QGroupBox("검사 방향 선택 (체크 순서대로 진행, 기본: 상→하→좌→우)")
        select_layout = QHBoxLayout(select_group)
        self._direction_checks: dict[TravelDirection, QCheckBox] = {}
        for direction in (TravelDirection.UP, TravelDirection.DOWN, TravelDirection.LEFT, TravelDirection.RIGHT):
            cb = QCheckBox(_DIRECTION_LABELS[direction])
            cb.setChecked(True)
            self._direction_checks[direction] = cb
            select_layout.addWidget(cb)

        self.status_label = QLabel("대기 중")
        self.current_direction_label = QLabel("현재 방향: -")

        # 방향당 버튼은 이거 하나 - 다음에 시작할 방향 이름이 그대로 라벨에 표시됨
        self.start_direction_btn = QPushButton("검사 시작")
        self.start_direction_btn.clicked.connect(self._on_start_direction)

        dead_click_btn = QPushButton("데드클릭 발생")
        dead_click_btn.clicked.connect(self._on_dead_click)
        abort_btn = QPushButton("시험 중지 (미기록)")
        abort_btn.clicked.connect(self._on_abort)
        restart_all_btn = QPushButton("전체 재시작")
        restart_all_btn.clicked.connect(self._on_restart_all)

        interrupt_layout = QHBoxLayout()
        for btn in (dead_click_btn, abort_btn, restart_all_btn):
            interrupt_layout.addWidget(btn)

        # 재시험: 이미 끝난 방향 중 하나를 골라 다시 시험 (조작 실수가 의심될 때)
        retest_group = QGroupBox("재시험 (조작 실수 등으로 기록이 이상할 때)")
        self.retest_combo = QComboBox()
        retest_btn = QPushButton("선택한 방향 재시험")
        retest_btn.clicked.connect(self._on_retest)
        retest_layout = QHBoxLayout(retest_group)
        retest_layout.addWidget(self.retest_combo)
        retest_layout.addWidget(retest_btn)

        self.finalize_btn = QPushButton("시험 종료 (결과 저장)")
        self.finalize_btn.setEnabled(False)
        self.finalize_btn.clicked.connect(self._on_finalize)

        self.results_list = QListWidget()

        layout = QVBoxLayout(self)
        layout.addWidget(select_group)
        layout.addWidget(self.start_direction_btn)
        layout.addWidget(self.status_label)
        layout.addWidget(self.current_direction_label)
        layout.addLayout(interrupt_layout)
        layout.addWidget(retest_group)
        layout.addWidget(self.finalize_btn)
        layout.addWidget(QLabel("방향별 결과 / 시험 로그:"))
        layout.addWidget(self.results_list)

        self.vm.phase_changed.connect(self._on_phase_changed)
        self.vm.direction_completed.connect(self._on_direction_completed)
        self.vm.inspection_completed.connect(self._on_inspection_completed)
        self.vm.inspection_finalized.connect(self._on_inspection_finalized)

    def _selected_directions(self) -> list[TravelDirection]:
        return [d for d, cb in self._direction_checks.items() if cb.isChecked()]

    def _on_start_direction(self) -> None:
        phase = self.vm.state_machine.phase
        if phase == Phase.IDLE:
            if not self.vm.can_start_inspection():
                self.status_label.setText("부품 ID를 먼저 입력하세요 - 시험을 시작할 수 없습니다.")
                return
            self.vm.configure_directions(self._selected_directions())
        self._advance()

    def _advance(self) -> None:
        if not self.vm.state_machine.has_next_direction():
            return
        try:
            direction = self.vm.start_next_direction()
            self.current_direction_label.setText(f"현재 방향: {_DIRECTION_LABELS[direction]}")
            self.status_label.setText("이동 중 - 목표(35MOA) 근처에서 잠깐 멈추면 자동 평가됩니다")
            self.start_direction_btn.setEnabled(False)
        except RuntimeError as exc:
            self.status_label.setText(str(exc))

    def _on_dead_click(self) -> None:
        self.vm.flag_dead_click()

    def _on_abort(self) -> None:
        self.vm.abort_current_direction()
        self.status_label.setText("시험 중지됨 (미기록) - 재시작 가능")
        self.current_direction_label.setText("현재 방향: -")
        self.start_direction_btn.setEnabled(True)

    def _on_restart_all(self) -> None:
        self.vm.restart_all()
        self.results_list.clear()
        self.retest_combo.clear()
        self.status_label.setText("전체 재시작됨")
        self.current_direction_label.setText("현재 방향: -")
        self.start_direction_btn.setText("검사 시작")
        self.start_direction_btn.setEnabled(True)
        self.finalize_btn.setEnabled(False)

    def _on_retest(self) -> None:
        text = self.retest_combo.currentText()
        direction = next((d for d, label in _DIRECTION_LABELS.items() if label == text), None)
        if direction is None:
            return
        try:
            self.vm.retest_direction(direction)
        except RuntimeError as exc:
            self.status_label.setText(str(exc))
            return
        self.status_label.setText(f"{text} 방향 재시험 대기 중 - 검사 시작을 누르세요")
        self.start_direction_btn.setEnabled(True)
        self.finalize_btn.setEnabled(False)

    def _on_finalize(self) -> None:
        overall = self.vm.finalize_inspection()
        self.status_label.setText(f"저장 완료 - 최종 판정: {overall}")
        self.finalize_btn.setEnabled(False)

    def _on_phase_changed(self, phase_name: str) -> None:
        if phase_name == Phase.RETURN.name:
            self.status_label.setText("원점으로 복귀 중 - 원점 근처에서 잠깐 멈추면 자동 평가됩니다")
        elif phase_name == Phase.DIRECTION_DONE.name:
            self.current_direction_label.setText("현재 방향: -")
            if self.vm.state_machine.has_next_direction():
                self.start_direction_btn.setEnabled(True)
                next_direction = self.vm.state_machine.direction_queue[0]
                self.start_direction_btn.setText(f"{_DIRECTION_LABELS[next_direction]} 시험 시작")
                self.status_label.setText("방향 완료 - 다음 방향 시작을 누르세요")

    def _on_direction_completed(self, result: DirectionTestResult) -> None:
        checks_summary = ", ".join(f"{c.check_type.value}={c.status.value}" for c in result.check_results)
        self.results_list.addItem(
            f"[{_DIRECTION_LABELS[result.direction]}] 시도#{result.attempt_number} "
            f"{result.verdict.value} ({checks_summary})"
        )
        label = _DIRECTION_LABELS[result.direction]
        if self.retest_combo.findText(label) < 0:
            self.retest_combo.addItem(label)
        if self.vm.is_ready_to_finalize:
            self.finalize_btn.setEnabled(True)

    def _on_inspection_completed(self, overall_verdict: str) -> None:
        self.status_label.setText(f"모든 방향 완료 - 전체 판정: {overall_verdict} (시험 종료를 눌러 저장하세요)")
        self.current_direction_label.setText("현재 방향: -")
        self.start_direction_btn.setEnabled(False)
        if self.vm.is_ready_to_finalize:
            self.finalize_btn.setEnabled(True)

    def _on_inspection_finalized(self, overall_verdict: str) -> None:
        self.retest_combo.clear()
        self.finalize_btn.setEnabled(False)
        self.start_direction_btn.setEnabled(False)
