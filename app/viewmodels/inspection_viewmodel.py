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
        self.tracker = BlobTracker(
            max_jump_px=settings.detection.max_blob_jump_px,
            elongation_correction_threshold=settings.detection.elongation_correction_threshold,
            elongation_correction_blend=settings.detection.elongation_correction_blend,
        )
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
        # 원본 프레임을 그대로 내보낸다 - 격자 오버레이/원점·레드닷 마커/크롭/캘리브레이션
        # 모드(원본 그대로+클릭 스냅)는 모두 LiveFeedView 하나가 화면 좌측에 항상 떠 있으면서
        # 현재 활성 탭에 따라 모드를 바꿔가며 그린다(MainWindow.set_calibration_mode() 참고).
        # 뷰모델이 프레임 자체를 가공하면 다른 모드에도 영향을 주게 되어 여기서는 순수 전달만 담당한다.
        self.frame_ready.emit(frame_bgr)

        # 폴더 재생 모드(PlaybackCameraService)처럼 프레임끼리 시간적 연속성이 없는
        # 소스는 매 프레임을 "새로 시작"으로 취급해야 한다 - BlobTracker.select()는
        # 이전 프레임 위치에서 max_jump_px 이내인 블롭만 채택하는데, 서로 무관한 이미지들
        # 사이에서는 레드닷 위치가 수백 px씩 "점프"해서 첫 프레임 이후로는 전부 거부되는
        # 문제가 있었다(실측으로 확인, 2026-09-14).
        if getattr(self.camera, "reset_tracker_each_frame", False):
            self.tracker.reset()

        # 캘리브레이션(원점+스케일)이 확정된 후에는 레드닷이 벗어날 수 없는 범위가 분명하므로
        # 그 범위로만 검출을 제한한다 - 화면 먼 쪽의 문자/눈금 반사가 애초에 후보에서
        # 제외되고, 처리할 픽셀 수도 줄어 프레임당 처리 시간이 준다(사용자 요청, 2026-09-14).
        # 미확정 상태(원점 탐색 전 등)에서는 범위를 모르므로 전체 프레임을 그대로 검색한다.
        roi_px = None
        if self.calibration.is_ready:
            profile = self.calibration.profile
            margin_moa = self.settings.detection.roi_margin_moa
            half_w_px = margin_moa * profile.px_per_moa_x
            half_h_px = margin_moa * profile.px_per_moa_y
            roi_px = (
                profile.origin_px_x - half_w_px,
                profile.origin_px_y - half_h_px,
                profile.origin_px_x + half_w_px,
                profile.origin_px_y + half_h_px,
            )

        candidates = self.detector.detect(frame_bgr, roi_px=roi_px)

        out_of_range = False
        if not candidates and roi_px is not None:
            # 조립 상태에 따라 레드닷이 정상 이동 범위(roi_margin_moa) 밖에 있는 경우가
            # 있다고 한다 - 이때는 사용자가 수동 조정으로 원점 쪽으로 가져와야 하는데,
            # 그러려면 지금 레드닷이 어디 있는지부터 보여줘야 한다. 범위 안에서 못 찾으면
            # (밝기 기준은 동일하게 적용해) 전체 프레임에서 한 번 더 찾아본다(사용자 요청,
            # 2026-09-15).
            candidates = self.detector.detect(frame_bgr, roi_px=None)
            out_of_range = bool(candidates)

        if out_of_range:
            # 범위 밖 결과는 화면 표시 전용이다 - BlobTracker의 이전 위치 기반 연속성
            # 게이팅이나 상태기계 이동량 판정에 섞이면 안 된다(조립 조정 중의 위치를 실제
            # 시험 이동으로 오인하게 됨). area 내림차순으로 이미 정렬돼 있으므로 가장 큰
            # 후보를 트래커를 거치지 않고 그대로 쓴다.
            best = candidates[0]
            result = DetectionResult(
                found=True,
                center_px=best.center_px,
                ellipse=best.ellipse,
                core_circle=best.core_circle,
                contour=best.contour,
                area_px2=best.area_px2,
                out_of_range=True,
            )
        else:
            result = self.tracker.select(candidates)
        self.detection_ready.emit(result)

        # profile이 있어도 스케일(px_per_moa)이 자동 검출 직후의 임시값(1.0)일 수 있다 -
        # is_ready(원점+x/y 스케일 모두 확정)가 아니면 to_moa() 결과가 터무니없는 값이 되어
        # state_machine에 잘못된 이동량/드리프트/쉬프트/백래쉬 판정을 유발할 수 있으므로
        # (실측으로 확인된 문제, 2026-09-14) 스케일 확정 전에는 아예 피드하지 않는다.
        # out_of_range 결과도 같은 이유로 피드하지 않는다(위 주석 참고).
        if result.found and not result.out_of_range and self.calibration.is_ready:
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
