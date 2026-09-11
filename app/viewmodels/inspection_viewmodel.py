"""UI(Views)와 core 로직을 연결하는 뷰모델.

PySide6 Signal/Slot으로 core의 콜백 기반 API를 Qt 이벤트 루프에 맞게 감싼다.
core 쪽 클래스(TravelTestStateMachine, RedDotDetector 등)는 Qt를 몰라도 되게 유지한다.
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QObject, Signal

from core.calibration.pixel_angle_calibration import PixelAngleCalibration
from core.camera.camera_service import ICameraService
from core.camera.frame_bus import FrameBus
from core.config.settings import Settings
from core.inspection.models import TravelDirection, Verdict
from core.inspection.travel_test_state_machine import Phase, TravelTestStateMachine
from core.tracking.position_sample import PositionSample
from core.vision.blob_tracker import BlobTracker
from core.vision.red_dot_detector import DetectionResult, RedDotDetector


class InspectionViewModel(QObject):
    frame_ready = Signal(np.ndarray)
    detection_ready = Signal(object)  # DetectionResult
    phase_changed = Signal(str)
    direction_completed = Signal(object)  # DirectionTestResult
    inspection_completed = Signal(str)  # overall verdict 문자열

    def __init__(self, camera: ICameraService, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.camera = camera
        self.settings = settings

        self.frame_bus = FrameBus()
        self.detector = RedDotDetector(settings.detection)
        self.tracker = BlobTracker(max_jump_px=settings.detection.max_blob_jump_px)
        self.calibration = PixelAngleCalibration(mrad_to_moa_ratio=settings.calibration.mrad_to_moa_ratio)
        self.state_machine = TravelTestStateMachine(settings.stage2)

        self.scope_id: str | None = None
        self._grid_overlay_enabled = False

        self.frame_bus.subscribe(self._on_frame)

    # ---- 카메라 제어 ----
    def start_camera(self) -> None:
        self.camera.open()
        self.camera.apply_settings(self.settings.camera)
        self.camera.start(self.frame_bus.publish)

    def stop_camera(self) -> None:
        self.camera.stop()
        self.camera.close()

    def set_grid_overlay_enabled(self, enabled: bool) -> None:
        self._grid_overlay_enabled = enabled

    # ---- 시험 시작 게이트 ----
    def can_start_inspection(self) -> bool:
        return bool(self.scope_id and self.scope_id.strip())

    def set_scope_id(self, scope_id: str) -> None:
        self.scope_id = scope_id

    # ---- 2단계 방향 큐 제어 (UI에서 호출) ----
    def configure_directions(self, directions: list[TravelDirection]) -> None:
        self.state_machine.configure(directions)

    def start_next_direction(self) -> TravelDirection:
        direction = self.state_machine.start_next_direction()
        self.phase_changed.emit(self.state_machine.phase.name)
        return direction

    def mark_far_point_reached(self) -> None:
        self.state_machine.mark_far_point_reached()
        self._after_state_change()

    def mark_returned_to_origin(self) -> None:
        self.state_machine.mark_returned_to_origin()
        self._after_state_change()

    def flag_dead_click(self) -> None:
        self.state_machine.flag_dead_click()
        self._after_state_change()

    def abort_current_direction(self) -> None:
        self.state_machine.abort_current_direction()
        self.phase_changed.emit(self.state_machine.phase.name)

    def restart_all(self) -> None:
        self.state_machine.restart_all()
        self.phase_changed.emit(self.state_machine.phase.name)

    def _after_state_change(self) -> None:
        self.phase_changed.emit(self.state_machine.phase.name)
        if self.state_machine.direction_results:
            self.direction_completed.emit(self.state_machine.direction_results[-1])
        if self.state_machine.phase == Phase.INSPECTION_DONE:
            self.inspection_completed.emit(self.state_machine.overall_verdict.value)

    # ---- 프레임 처리 ----
    def _on_frame(self, frame_bgr: np.ndarray) -> None:
        display_frame = frame_bgr
        if self._grid_overlay_enabled and self.calibration.profile is not None:
            from core.calibration.grid_overlay import draw_moa_grid_overlay

            display_frame = draw_moa_grid_overlay(frame_bgr, self.calibration.profile)

        self.frame_ready.emit(display_frame)

        candidates = self.detector.detect(frame_bgr)
        result: DetectionResult = self.tracker.select(candidates)
        self.detection_ready.emit(result)

        if result.found and self.calibration.profile is not None:
            x_moa, y_moa = self.calibration.to_moa(result.center_px)
            sample = PositionSample(timestamp_s=time.time(), x_moa=x_moa, y_moa=y_moa)
            self.state_machine.feed_position(sample)
