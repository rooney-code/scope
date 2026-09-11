"""2단계: 트래블 검사 실행 화면.

방향 선택/순서 UI, '이동 완료'/'데드클릭 발생'/'시험 중지'/'재시작' 버튼, 원점 복귀 안내,
실시간 체크 상태 패널.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
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

        start_btn = QPushButton("검사 시작")
        start_btn.clicked.connect(self._on_start)

        self.status_label = QLabel("대기 중")
        self.current_direction_label = QLabel("현재 방향: -")

        far_point_btn = QPushButton("이동 완료 (목표 지점 도달)")
        far_point_btn.clicked.connect(self._on_far_point_reached)
        origin_btn = QPushButton("원점 복귀 완료")
        origin_btn.clicked.connect(self._on_returned_to_origin)
        dead_click_btn = QPushButton("데드클릭 발생")
        dead_click_btn.clicked.connect(self._on_dead_click)
        abort_btn = QPushButton("시험 중지 (미기록)")
        abort_btn.clicked.connect(self._on_abort)
        restart_all_btn = QPushButton("전체 재시작")
        restart_all_btn.clicked.connect(self._on_restart_all)

        action_layout = QHBoxLayout()
        for btn in (far_point_btn, origin_btn, dead_click_btn, abort_btn, restart_all_btn):
            action_layout.addWidget(btn)

        self.results_list = QListWidget()

        layout = QVBoxLayout(self)
        layout.addWidget(select_group)
        layout.addWidget(start_btn)
        layout.addWidget(self.status_label)
        layout.addWidget(self.current_direction_label)
        layout.addLayout(action_layout)
        layout.addWidget(QLabel("방향별 결과:"))
        layout.addWidget(self.results_list)

        self.vm.phase_changed.connect(self._on_phase_changed)
        self.vm.direction_completed.connect(self._on_direction_completed)
        self.vm.inspection_completed.connect(self._on_inspection_completed)

    def _selected_directions(self) -> list[TravelDirection]:
        return [d for d, cb in self._direction_checks.items() if cb.isChecked()]

    def _on_start(self) -> None:
        if not self.vm.can_start_inspection():
            self.status_label.setText("부품 ID를 먼저 입력하세요 - 시험을 시작할 수 없습니다.")
            return
        self.vm.configure_directions(self._selected_directions())
        self._advance()

    def _advance(self) -> None:
        if self.vm.state_machine.has_next_direction() or self.vm.state_machine.phase == Phase.IDLE:
            try:
                direction = self.vm.start_next_direction()
                self.current_direction_label.setText(f"현재 방향: {_DIRECTION_LABELS[direction]}")
                self.status_label.setText("이동 중 (0 -> 목표)")
            except RuntimeError as exc:
                self.status_label.setText(str(exc))

    def _on_far_point_reached(self) -> None:
        self.vm.mark_far_point_reached()

    def _on_returned_to_origin(self) -> None:
        self.vm.mark_returned_to_origin()

    def _on_dead_click(self) -> None:
        self.vm.flag_dead_click()

    def _on_abort(self) -> None:
        self.vm.abort_current_direction()
        self.status_label.setText("시험 중지됨 (미기록) - 재시작 가능")
        self.current_direction_label.setText("현재 방향: -")

    def _on_restart_all(self) -> None:
        self.vm.restart_all()
        self.results_list.clear()
        self.status_label.setText("전체 재시작됨")
        self.current_direction_label.setText("현재 방향: -")

    def _on_phase_changed(self, phase_name: str) -> None:
        if phase_name == Phase.RETURN.name:
            self.status_label.setText("복귀 중 (목표 -> 0)")
        elif phase_name == Phase.DIRECTION_DONE.name:
            self.status_label.setText("방향 완료 - 다음 방향으로 진행하거나 재시작하세요")

    def _on_direction_completed(self, result: DirectionTestResult) -> None:
        checks_summary = ", ".join(f"{c.check_type.value}={c.status.value}" for c in result.check_results)
        self.results_list.addItem(
            f"[{_DIRECTION_LABELS[result.direction]}] 시도#{result.attempt_number} "
            f"{result.verdict.value} ({checks_summary})"
        )
        if self.vm.state_machine.phase == Phase.DIRECTION_DONE and self.vm.state_machine.has_next_direction():
            self._advance()

    def _on_inspection_completed(self, overall_verdict: str) -> None:
        self.status_label.setText(f"검사 종료 - 최종 판정: {overall_verdict}")
        self.current_direction_label.setText("현재 방향: -")
