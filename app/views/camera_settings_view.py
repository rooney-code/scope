"""카메라 파라미터 화면. 현장 캡처 값(계획서 표)을 기본값으로 로드하고,
'기본값 복원' 버튼으로 언제든 되돌릴 수 있다."""
from __future__ import annotations

from dataclasses import fields

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.camera.camera_service import ICameraService
from core.config.settings import CameraSettings


_ON_OFF_FIELDS = {
    "black_level_auto",
    "auto_exposure",
    "auto_gain",
    "auto_white_balance",
    "color_correction_host",
}


class CameraSettingsView(QWidget):
    def __init__(self, camera: ICameraService, settings: CameraSettings, parent=None) -> None:
        super().__init__(parent)
        self.camera = camera
        self.settings = settings
        self._widgets: dict[str, QWidget] = {}

        form = QFormLayout()
        for f in fields(CameraSettings):
            widget = self._make_widget(f.name, getattr(settings, f.name))
            self._widgets[f.name] = widget
            form.addRow(f.name, widget)

        apply_btn = QPushButton("적용 (카메라에 반영)")
        apply_btn.clicked.connect(self._on_apply)
        restore_btn = QPushButton("기본값 복원")
        restore_btn.clicked.connect(self._on_restore_defaults)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(apply_btn)
        layout.addWidget(restore_btn)

    def _make_widget(self, name: str, value) -> QWidget:
        if name in _ON_OFF_FIELDS:
            combo = QComboBox()
            combo.addItems(["off", "on", "host", "once"])
            combo.setCurrentText(str(value))
            return combo
        if isinstance(value, bool):
            box = QCheckBox()
            box.setChecked(value)
            return box
        if isinstance(value, (int, float)):
            spin = QDoubleSpinBox()
            spin.setRange(-1_000_000_000, 1_000_000_000)
            spin.setDecimals(4)
            spin.setValue(float(value))
            return spin
        line = QLineEdit(str(value))
        return line

    def _read_widget(self, name: str, widget: QWidget, default):
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QDoubleSpinBox):
            return type(default)(widget.value()) if isinstance(default, int) else widget.value()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return default

    def _on_apply(self) -> None:
        for f in fields(CameraSettings):
            current_default = getattr(self.settings, f.name)
            new_value = self._read_widget(f.name, self._widgets[f.name], current_default)
            setattr(self.settings, f.name, new_value)
        self.camera.apply_settings(self.settings)

    def _on_restore_defaults(self) -> None:
        defaults = CameraSettings()
        for f in fields(CameraSettings):
            default_value = getattr(defaults, f.name)
            setattr(self.settings, f.name, default_value)
            widget = self._widgets[f.name]
            if isinstance(widget, QComboBox):
                widget.setCurrentText(str(default_value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(default_value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(default_value))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(default_value))
