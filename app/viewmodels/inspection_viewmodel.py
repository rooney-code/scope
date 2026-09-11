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
from core.inspection.models import InspectionSession, TravelDirection, Verdict
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
    inspection_finalized = Signal(str)  # "시험 종료" 버튼으로 DB 저장 완료 - overall verdict 문자열

    def __init__(self, camera: ICameraService, settings: Settings, repository=None, parent=None) -> None:
        super().__init__(parent)
        self.camera = camera
        self.settings = settings
        self.repository = repository  # None이면 저장 없이 finalize()만 수행(하드웨어/DB 없는 개발 모드)

        self.frame_bus = FrameBus()
        self.detector = RedDotDetector(settings.detection)
        self.tracker = BlobTracker(max_jump_px=settings.detection.max_blob_jump_px)
        self.calibration = PixelAngleCalibration(mrad_to_moa_ratio=settings.calibration.mrad_to_moa_ratio)
        self.state_machine = TravelTestStateMachine(settings.stage2)

        self.scope_id: str | None = None

        self.frame_bus.subscribe(self._on_frame)

    # ---- 카메라 제어 ----
    def start_camera(self) -> None:
        self.camera.open()
        self.camera.apply_settings(self.settings.camera)
        self.camera.start(self.frame_bus.publish)

    def stop_camera(self) -> None:
        self.camera.stop()
        self.camera.close()

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

    def start_direction(self, direction: TravelDirection) -> TravelDirection:
        """방향별 박스의 시작/재시작 버튼 - 순서 큐 없이 어떤 방향이든 바로 (재)시작한다.
        다른 방향이 진행 중이었다면 그 미완성 데이터는 폐기된다(상태기계가 처리)."""
        result = self.state_machine.start_direction(direction)
        self.phase_changed.emit(self.state_machine.phase.name)
        return result

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

    def retest_direction(self, direction: TravelDirection) -> None:
        """작업자의 조작 실수 등으로 특정 방향을 다시 시험하고 싶을 때 - 이전 기록은 즉시
        사라지고(이력 보존 없음, 아직 DB에 저장 전이므로), 그 방향+아직 못한 방향들이 다시
        큐에 들어가 이어서 진행된다."""
        self.state_machine.retest_direction(direction)
        self.phase_changed.emit(self.state_machine.phase.name)

    @property
    def is_ready_to_finalize(self) -> bool:
        return self.state_machine.is_ready_to_finalize

    def finalize_inspection(self) -> str:
        """'시험 종료' 버튼 - 결과를 확정하고(이후 재시험 불가) repository가 있으면 DB에
        저장한다. repository가 없으면(하드웨어/DB 미연결 개발 모드) 확정만 하고 넘어간다.
        반환값: 최종 전체 판정 문자열."""
        self.state_machine.finalize()
        if self.repository is not None and self.scope_id:
            session_id = self.repository.create_session(self.scope_id, operator="")
            session = InspectionSession(
                scope_id=self.scope_id,
                direction_results=self.state_machine.direction_results,
                overall_verdict=self.state_machine.overall_verdict,
            )
            self.repository.save_full_session(session, session_id)
        overall_verdict = self.state_machine.overall_verdict.value
        self.inspection_finalized.emit(overall_verdict)
        return overall_verdict

    def _after_state_change(self) -> None:
        self.phase_changed.emit(self.state_machine.phase.name)
        if self.state_machine.direction_results:
            self.direction_completed.emit(self.state_machine.direction_results[-1])
        if self.state_machine.phase == Phase.INSPECTION_DONE:
            self.inspection_completed.emit(self.state_machine.overall_verdict.value)

    # ---- 프레임 처리 ----
    def _on_frame(self, frame_bgr: np.ndarray) -> None:
        # 원본 프레임을 그대로 내보낸다 - 격자 오버레이/원점·레드닷 마커/크롭은 각 View가
        # 자신의 용도에 맞게 그린다(예: LiveFeedView는 격자 토글+원점 크롭, CalibrationView는
        # 원본 그대로 보여줌). 뷰모델이 프레임 자체를 가공하면 다른 View에도 영향을 주게 되어
        # 여기서는 순수 전달만 담당한다.
        self.frame_ready.emit(frame_bgr)

        candidates = self.detector.detect(frame_bgr)
        result: DetectionResult = self.tracker.select(candidates)
        self.detection_ready.emit(result)

        if result.found and self.calibration.profile is not None:
            x_moa, y_moa = self.calibration.to_moa(result.center_px)
            sample = PositionSample(timestamp_s=time.time(), x_moa=x_moa, y_moa=y_moa)

            # feed_position() 자체가 목표/원점 근처에서의 멈춤을 감지해 이동량/쉬프트/드리프트/
            # 백래쉬 평가와 방향 전환("이동 완료"/"원점 복귀 완료" 버튼 없이)까지 자동으로
            # 수행할 수 있으므로, 매 프레임 이후 상태가 실제로 바뀌었는지 확인해서 그때만
            # 시그널을 내보낸다(매 프레임 emit하면 UI에 불필요한 갱신이 계속 발생함).
            phase_before = self.state_machine.phase
            result_count_before = len(self.state_machine.direction_results)
            self.state_machine.feed_position(sample)
            if self.state_machine.phase != phase_before or len(self.state_machine.direction_results) != result_count_before:
                self._after_state_change()
