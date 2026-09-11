"""결과 조회 화면: ID 검색, 이력 조회, CSV 내보내기."""
from __future__ import annotations

import csv

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ResultsView(QWidget):
    def __init__(self, repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self._last_rows: list[dict] = []

        self.scope_id_input = QLineEdit()
        self.scope_id_input.setPlaceholderText("조회할 부품(조준경) ID")
        search_btn = QPushButton("조회")
        search_btn.clicked.connect(self._on_search)
        export_btn = QPushButton("CSV 내보내기")
        export_btn.clicked.connect(self._on_export_csv)

        search_layout = QHBoxLayout()
        search_layout.addWidget(self.scope_id_input)
        search_layout.addWidget(search_btn)
        search_layout.addWidget(export_btn)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["SessionId", "ScopeId", "Operator", "StartedAt", "CompletedAt", "OverallVerdict"]
        )

        layout = QVBoxLayout(self)
        layout.addLayout(search_layout)
        layout.addWidget(self.table)

    def _on_search(self) -> None:
        scope_id = self.scope_id_input.text().strip()
        if not scope_id:
            return
        try:
            rows = self.repository.get_sessions_by_scope(scope_id)
        except Exception as exc:  # noqa: BLE001 - DB 연결 문제 등을 화면에 표시
            self._last_rows = []
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem(f"조회 실패: {exc}"))
            return

        self._last_rows = rows
        self.table.setRowCount(len(rows))
        columns = ["SessionId", "ScopeId", "Operator", "StartedAt", "CompletedAt", "OverallVerdict"]
        for r, row in enumerate(rows):
            for c, col in enumerate(columns):
                self.table.setItem(r, c, QTableWidgetItem(str(row.get(col, ""))))

    def _on_export_csv(self) -> None:
        if not self._last_rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "CSV로 저장", "results.csv", "CSV Files (*.csv)")
        if not path:
            return
        columns = ["SessionId", "ScopeId", "Operator", "StartedAt", "CompletedAt", "OverallVerdict"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in self._last_rows:
                writer.writerow({c: row.get(c, "") for c in columns})
