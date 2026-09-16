"""UI(Views)와 core 로직을 연결하는 뷰모델.

PySide6 Signal/Slot으로 core의 콜백 기반 API를 Qt 이벤트 루프에 맞게 감싼다.
core 쪽 클래스(TravelTestStateMachine, RedDotDetector 등)는 Qt를 몰라도 되게 유지한다.
"""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QObject, Signal

from core.calibration.pixel_angle_calibration import PixelAngleCalibration
from core.camera.camera_service import ICameraService
from core.camera.frame_bus import FrameBus
from core.camera.playback_camera_service import PlaybackCameraService
from core.config.settings import Settings
from core.data.text_report import write_session_txt
from core.inspection.models import InspectionSession, Stage2LiveState, TravelDirection, Verdict
from core.inspection.travel_test_state_machine import Phase, TravelTestStateMachine
from core.tracking.position_sample import PositionSample
from core.tracking.stability_detector import StabilityDetector
from core.vision.blob_tracker import BlobTracker
from core.vision.red_dot_detector import DetectionResult, RedDotDetector

# 시험 방향(TravelDirection) -> 화면 픽셀 방향 힌트(부호만 의미 있음). "위(up)가 +Y"라는
# 도메인 규약(PixelAngleCalibration.to_moa 참고: 화면 y는 아래로 증가하지만 위가 +Y이므로
# 부호를 반전)에 따라 위 방향은 픽셀 y가 감소하는 쪽이다. RedDotDetector._fit_head_square가
# 코멧테일의 머리/꼬리 밝기 차이가 애매할 때, 현재 시험 중인 방향으로 레드닷이 멀어지며
# 늘어진다고 보고 무게중심을 그쪽으로 살짝 옮기는 데 쓴다(사용자 제안, 2026-09-15).
_DIRECTION_TO_PIXEL_HINT: dict[TravelDirection, tuple[float, float]] = {
    TravelDirection.UP: (0.0, -1.0),
    TravelDirection.DOWN: (0.0, 1.0),
    TravelDirection.LEFT: (-1.0, 0.0),
    TravelDirection.RIGHT: (1.0, 0.0),
}


class InspectionViewModel(QObject):
    frame_ready = Signal(np.ndarray)
    detection_ready = Signal(object)  # DetectionResult
    phase_changed = Signal(str)
    direction_completed = Signal(object)  # DirectionTestResult
    stage2_live_update = Signal(object)  # Stage2LiveState
    inspection_completed = Signal(str)  # overall verdict 문자열
    inspection_finalized = Signal(str)  # "시험 종료" 버튼으로 DB 저장 완료 - overall verdict 문자열
    # 수신 FPS(카메라에서 실제로 들어오는 속도), 처리 FPS(검출+상태기계를 실제로 처리한 속도) -
    # 적응형 프레임 스킵이 실제로 얼마나 건너뛰고 있는지 라이브 화면에서 보고 싶다는 요청
    # (2026-09-16)에 따라 추가.
    fps_stats_updated = Signal(float, float)

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
            max_reference_age_s=settings.detection.max_position_reference_age_s,
        )
        self.calibration = PixelAngleCalibration(mrad_to_moa_ratio=settings.calibration.mrad_to_moa_ratio)
        # settings.stability는 예전엔 로드만 되고 실제로 아무 데도 연결 안 된 죽은 설정이었다
        # (TravelTestStateMachine이 150ms로 하드코딩된 자체 기본값을 썼음) - 이번에 연결한다
        # (사용자 요청, 2026-09-16). 원점 복귀 자동 판정/대기 중 baseline 추적에 쓰인다.
        stability_settings = settings.stability
        self.state_machine = TravelTestStateMachine(
            settings.stage2,
            stability_detector_factory=lambda: StabilityDetector(
                window_size_samples=stability_settings.window_size_samples,
                variance_threshold_moa2=stability_settings.variance_threshold_moa2,
                # StabilitySettings는 초(sec) 단위(설정 화면에서 다루기 쉽게, 사용자 요청
                # 2026-09-16)지만 StabilityDetector 내부는 여전히 ms 단위이므로 여기서 변환.
                min_stable_duration_ms=stability_settings.min_stable_duration_s * 1000.0,
            ),
        )

        self.scope_id: str | None = None
        # "시험 시작" 버튼을 눌러야 대기 baseline 추적(자동 방향 인식)이 시작된다 - 계산 준비
        # 전(부품 ID 미입력 등)에 미리 추적이 시작되지 않게 하기 위함(사용자 요청, 2026-09-16).
        self._session_active: bool = False
        # 프레임당 처리(검출+상태기계) 시간의 지수이동평균(초) - 실장비 없이 녹화 영상으로
        # 절차를 검증할 때, 처리 속도가 영상의 실제 fps를 못 따라가면 얼마나 못 따라가는지
        # 화면에 보여주기 위함(사용자 요청, 2026-09-15). 개발 PC에서는 평균 3ms 정도지만
        # 실제 운용 PC는 더 느릴 수 있다는 우려(사용자 요청, 2026-09-16)에 따라, 이 값을
        # 기반으로 적응형 프레임 스킵도 여기서 함께 관리한다(_current_frame_skip_n 참고).
        self._avg_processing_time_s: float | None = None
        self._frame_counter: int = 0
        # 수신/처리 FPS(지수이동평균) - fps_stats_updated 참고. 수신 쪽은 매 _on_frame() 호출마다,
        # 처리 쪽은 실제로 검출/상태기계를 돌린 프레임에서만 갱신된다.
        self._last_frame_arrival_s: float | None = None
        self._received_fps: float = 0.0
        self._last_processed_arrival_s: float | None = None
        self._processed_fps: float = 0.0
        self._last_fps_emit_s: float | None = None

        self.frame_bus.subscribe(self._on_frame)

    # ---- 카메라 제어 ----
    def start_camera(self) -> None:
        self.camera.open()
        self.camera.apply_settings(self.settings.camera)
        self.camera.start(self.frame_bus.publish)

    def stop_camera(self) -> None:
        self.camera.stop()
        self.camera.close()

    def load_simulation_video(self, path: str) -> None:
        """실장비 없이 녹화 영상(.avi 등)으로 시험 절차를 검증하기 위해, 지금 카메라가
        무엇이든(실카메라/Mock/기존 재생) 멈추고 이 영상 기반 재생으로 교체한다. 로드
        직후에는 일시정지 상태로 시작해 작업자가 준비된 뒤 직접 재생을 눌러야 한다
        (사용자 요청, 2026-09-15)."""
        self.stop_camera()
        self.camera = PlaybackCameraService(path, loop=False)
        self.camera.open()
        self.camera.apply_settings(self.settings.camera)
        self.camera.start(self.frame_bus.publish)
        self.camera.pause()

    def play_simulation_video(self) -> None:
        if hasattr(self.camera, "resume"):
            self.camera.resume()

    def pause_simulation_video(self) -> None:
        if hasattr(self.camera, "pause"):
            self.camera.pause()

    def seek_simulation_video(self, frame_index: int) -> None:
        """재생 위치를 특정 프레임으로 직접 이동한다(프로그레스바 탐색) - 재생/일시정지
        버튼만으로는 특정 지점을 찾아가기 번거롭다는 요청(2026-09-15)에 따른 기능.

        BlobTracker가 참조하는 "직전 위치"는 일정 시간(기본 1초, BlobTracker.
        max_reference_age_s)이 지나면 스스로 낡은 것으로 취급해 게이팅을 건너뛰므로
        (탐색처럼 조작에 시간이 걸리는 불연속 상황을 자동으로 흡수함, 2026-09-15) 여기서
        따로 트래커를 리셋하지 않는다."""
        if hasattr(self.camera, "seek"):
            self.camera.seek(frame_index)

    @property
    def avg_frame_processing_ms(self) -> float | None:
        return None if self._avg_processing_time_s is None else self._avg_processing_time_s * 1000.0

    @property
    def current_frame_skip_n(self) -> int:
        """지금 몇 프레임에 한 번씩 검출/상태기계를 처리하고 있는지(1=매 프레임 처리) -
        _current_frame_skip_n()과 동일한 계산이지만 UI가 표시용으로 읽을 수 있게 공개
        프로퍼티로 노출한다."""
        return self._current_frame_skip_n()

    # ---- 시험 시작 게이트 ----
    def can_start_inspection(self) -> bool:
        return bool(self.scope_id and self.scope_id.strip())

    def start_inspection_session(self) -> None:
        """"시험 시작" 버튼 - 대기 baseline 추적(자동 방향 인식)을 개시한다. 부품 ID가
        비어 있으면 시작할 수 없다(can_start_inspection() 게이트)."""
        if not self.can_start_inspection():
            raise RuntimeError("부품 ID를 먼저 입력하세요 - 시험을 시작할 수 없습니다.")
        self._session_active = True

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

    def set_dead_click(self, direction: TravelDirection, flagged: bool) -> None:
        """종합 결과표의 O/X 토글 - 방향이 끝난 뒤 언제든 판정을 뒤집을 수 있다(사용자 요청,
        2026-09-16). 대상 방향이 아직 완료된 적 없으면 상태기계가 예외를 던진다."""
        self.state_machine.set_dead_click(direction, flagged)
        self._after_state_change()

    def abort_current_direction(self) -> None:
        self.state_machine.abort_current_direction()
        self.phase_changed.emit(self.state_machine.phase.name)

    def restart_all(self) -> None:
        self.state_machine.restart_all()
        self._session_active = False
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
        """'시험 종료' 버튼 - 결과를 확정하고(이후 재시험 불가) 텍스트 파일로 저장한다.
        repository가 있으면 DB에도 저장한다. DB는 나중에(기능 검증 후) 별도로 다시 붙이기로
        하고, 지금은 배포 대상 PC에 DB 설치 없이도 결과를 남길 수 있게 텍스트 저장을
        기본으로 한다(사용자 요청, 2026-09-16). 반환값: 최종 전체 판정 문자열."""
        self.state_machine.finalize()
        session = InspectionSession(
            scope_id=self.scope_id or "",
            direction_results=self.state_machine.direction_results,
            overall_verdict=self.state_machine.overall_verdict,
        )
        write_session_txt(session)
        if self.repository is not None and self.scope_id:
            session_id = self.repository.create_session(self.scope_id, operator="")
            self.repository.save_full_session(session, session_id)
        overall_verdict = self.state_machine.overall_verdict.value
        self.inspection_finalized.emit(overall_verdict)
        return overall_verdict

    def _after_state_change(self) -> None:
        # direction_completed를 phase_changed보다 먼저 내보낸다 - Stage2TravelTestView는 두
        # 시그널 모두에서 안내 메시지를 다시 계산하는데(_refresh_guidance), phase_changed가
        # 먼저 오면 "방금 방향이 끝났다"는 걸 아직 모르는 채로 한 번 계산해(예: "원점 정렬을
        # 위해 이동하세요") 잘못된 메시지가 아주 잠깐(같은 프레임 안에서) 로그에 남았다가
        # 바로 "완료" 처리되어 버리는 문제가 있었다(실측으로 확인, 2026-09-16: 34MOA에서
        # "이동 완료"를 눌러 이동량 부족으로 바로 종료됐을 때 "원점 정렬을 위해..."와 "백래쉬
        # 측정을 완료..."가 동시에 뜸). 순서를 바꿔 direction_completed 핸들러가 먼저
        # "방금 방향이 끝났다"는 상태를 기록해두면, 뒤이은 phase_changed의 재계산도 이미
        # 올바른(붙잡아두는) 메시지를 보게 된다.
        if self.state_machine.direction_results:
            self.direction_completed.emit(self.state_machine.direction_results[-1])
        self.phase_changed.emit(self.state_machine.phase.name)
        if self.state_machine.phase == Phase.INSPECTION_DONE:
            self.inspection_completed.emit(self.state_machine.overall_verdict.value)

    # ---- 프레임 처리 ----
    def _on_frame(self, frame_bgr: np.ndarray) -> None:
        # 원본 프레임을 그대로 내보낸다 - 격자 오버레이/원점·레드닷 마커/크롭/캘리브레이션
        # 모드(원본 그대로+클릭 스냅)는 모두 LiveFeedView 하나가 화면 좌측에 항상 떠 있으면서
        # 현재 활성 탭에 따라 모드를 바꿔가며 그린다(MainWindow.set_calibration_mode() 참고).
        # 뷰모델이 프레임 자체를 가공하면 다른 모드에도 영향을 주게 되어 여기서는 순수 전달만 담당한다.
        self.frame_ready.emit(frame_bgr)

        # 수신 FPS - 카메라 스레드가 실제로 프레임을 밀어넣는 속도. 건너뛴 프레임을 포함해
        # 매 _on_frame() 호출마다 계산은 하지만(가볍다), 화면에 보여주는 건 정보 표시용이라
        # 초 단위로 갱신해도 충분하다는 요청(2026-09-16)에 따라 시그널 emit 자체를
        # 1초에 한 번으로 제한한다(_maybe_emit_fps_stats) - 크로스 스레드 시그널 전달 빈도를
        # 줄여 부담을 더 낮춘다.
        now = time.perf_counter()
        if self._last_frame_arrival_s is not None:
            self._received_fps = self._update_fps_ema(self._received_fps, now - self._last_frame_arrival_s)
        self._last_frame_arrival_s = now
        self._maybe_emit_fps_stats(now)

        # 폴더 재생 모드(PlaybackCameraService)처럼 프레임끼리 시간적 연속성이 없는
        # 소스는 매 프레임을 "새로 시작"으로 취급해야 한다 - BlobTracker.select()는
        # 이전 프레임 위치에서 max_jump_px 이내인 블롭만 채택하는데, 서로 무관한 이미지들
        # 사이에서는 레드닷 위치가 수백 px씩 "점프"해서 첫 프레임 이후로는 전부 거부되는
        # 문제가 있었다(실측으로 확인, 2026-09-14).
        if getattr(self.camera, "reset_tracker_each_frame", False):
            self.tracker.reset()

        # 검출+상태기계 처리가 프레임 주기를 못 따라가는 느린 환경(개발 PC는 평균 3ms지만
        # 실제 운용 PC는 더 느릴 수 있다는 우려, 사용자 요청 2026-09-16)에서도 계속 밀리기만
        # 하지 않도록, 처리 시간이 프레임 주기보다 길어진 만큼만 이 무거운 나머지를 건너뛴다.
        # 영상 표시(frame_ready, 위에서 이미 내보냄)는 이 스킵과 무관하게 항상 매 프레임 그대로
        # 나간다 - 건너뛴 프레임 동안은 오버레이(레드닷 마커 등)만 마지막 처리 결과로 고정되고
        # 영상 자체는 끊기지 않는다("영상은 영상대로, 오버레이는 오버레이대로" - 사용자 확인,
        # 2026-09-16).
        self._frame_counter += 1
        if self._frame_counter % self._current_frame_skip_n() != 0:
            return

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

        # 프레임당 처리(검출~상태기계) 소요시간 측정 시작 - avg_frame_processing_ms 참고.
        processing_start = time.perf_counter()

        head_direction_hint_px = _DIRECTION_TO_PIXEL_HINT.get(self.state_machine.current_direction)
        candidates = self.detector.detect(frame_bgr, roi_px=roi_px, head_direction_hint_px=head_direction_hint_px)

        out_of_range = False
        if not candidates and roi_px is not None:
            # 조립 상태에 따라 레드닷이 정상 이동 범위(roi_margin_moa) 밖에 있는 경우가
            # 있다고 한다 - 이때는 사용자가 수동 조정으로 원점 쪽으로 가져와야 하는데,
            # 그러려면 지금 레드닷이 어디 있는지부터 보여줘야 한다. 범위 안에서 못 찾으면
            # (밝기 기준은 동일하게 적용해) 전체 프레임에서 한 번 더 찾아본다(사용자 요청,
            # 2026-09-15).
            candidates = self.detector.detect(frame_bgr, roi_px=None, head_direction_hint_px=head_direction_hint_px)
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

        if self.settings.detection.debug_logging:
            self._log_frame_outcome(result, candidates, out_of_range)

        self.detection_ready.emit(result)

        # profile이 있어도 스케일(px_per_moa)이 자동 검출 직후의 임시값(1.0)일 수 있다 -
        # is_ready(원점+x/y 스케일 모두 확정)가 아니면 to_moa() 결과가 터무니없는 값이 되어
        # state_machine에 잘못된 이동량/드리프트/쉬프트/백래쉬 판정을 유발할 수 있으므로
        # (실측으로 확인된 문제, 2026-09-14) 스케일 확정 전에는 아예 피드하지 않는다.
        # out_of_range 결과도 같은 이유로 피드하지 않는다(위 주석 참고).
        #
        # "시험 시작"을 누르기 전(_session_active=False)에는 대기 baseline 추적(자동 방향
        # 인식)이 시작되면 안 되지만, 이미 방향이 진행 중이면(current_direction이 있으면 -
        # 방향 버튼으로 수동 시작한 경우도 포함) 계속 피드해야 한다 - 그렇지 않으면 "시험
        # 시작"을 누르지 않고 방향 버튼만 눌러 진행하는 경우 이동량 추적이 영원히 멈춘다
        # (실측/스모크 테스트로 확인된 문제, 2026-09-16).
        should_feed = self.calibration.is_ready and (
            self._session_active or self.state_machine.current_direction is not None
        )
        if result.found and not result.out_of_range and should_feed:
            x_moa, y_moa = self.calibration.to_moa(result.center_px)
            sample = PositionSample(timestamp_s=time.time(), x_moa=x_moa, y_moa=y_moa)

            # feed_position() 자체가 목표/원점 근처에서의 멈춤을 감지해 이동량/쉬프트/드리프트/
            # 백래쉬 평가와 방향 전환("이동 완료"/"원점 복귀 완료" 버튼 없이)까지, 그리고 이제
            # 대기 중 방향 자동 인식까지 자동으로 수행할 수 있으므로, 매 프레임 이후 상태가
            # 실제로 바뀌었는지 확인해서 그때만 시그널을 내보낸다(매 프레임 emit하면 UI에
            # 불필요한 갱신이 계속 발생함).
            phase_before = self.state_machine.phase
            result_count_before = len(self.state_machine.direction_results)
            self.state_machine.feed_position(sample)
            if self.state_machine.phase != phase_before or len(self.state_machine.direction_results) != result_count_before:
                self._after_state_change()

            # 실시간 표시 줄(진행 중인 방향의 시작위치/현재위치/최대도달/최대편차) - 방향별
            # 박스 버튼이 더 이상 필수 조작이 아니게 되면서, 지금 무슨 일이 일어나고 있는지
            # 화면으로 볼 수 있어야 한다는 요청(2026-09-16)에 따라 매 프레임 emit한다.
            direction = self.state_machine.current_direction
            live_state = Stage2LiveState(
                current_direction=direction,
                current_x_moa=x_moa,
                current_y_moa=y_moa,
                baseline_moa=self.state_machine.baseline_moa if direction is not None else None,
                max_primary_reached_moa=self.state_machine.max_primary_reached_moa if direction is not None else None,
                max_abs_cross_moa=self.state_machine.max_abs_cross_moa if direction is not None else None,
                last_primary_moa=self.state_machine.last_primary_moa if direction is not None else None,
            )
            if self.settings.detection.debug_logging:
                self._log_stage2_state(live_state)
            self.stage2_live_update.emit(live_state)

        # 지수이동평균(alpha=0.2)으로 갱신 - 순간 튐(가비지 컬렉션 등)에 너무 민감하지
        # 않으면서도 최근 추세를 빠르게 반영한다.
        elapsed_s = time.perf_counter() - processing_start
        if self._avg_processing_time_s is None:
            self._avg_processing_time_s = elapsed_s
        else:
            self._avg_processing_time_s = 0.2 * elapsed_s + 0.8 * self._avg_processing_time_s

        # 처리 FPS - 실제로 검출/상태기계를 돌린 프레임 사이의 간격 기준(건너뛴 프레임은
        # 위에서 이미 return돼 여기 안 옴). 스킵 중일수록 수신 FPS보다 낮게 나타난다.
        now2 = time.perf_counter()
        if self._last_processed_arrival_s is not None:
            self._processed_fps = self._update_fps_ema(self._processed_fps, now2 - self._last_processed_arrival_s)
        self._last_processed_arrival_s = now2
        self._maybe_emit_fps_stats(now2)

    @staticmethod
    def _update_fps_ema(current_fps: float, interval_s: float) -> float:
        if interval_s <= 0:
            return current_fps
        instantaneous_fps = 1.0 / interval_s
        return instantaneous_fps if current_fps == 0.0 else 0.2 * instantaneous_fps + 0.8 * current_fps

    def _maybe_emit_fps_stats(self, now: float) -> None:
        """fps_stats_updated는 화면 표시용 정보라 초 단위로만 갱신해도 충분하다는 요청
        (2026-09-16)에 따라, 실제 emit은 최대 1초에 한 번으로 제한한다 - EMA 계산 자체는
        (가벼우므로) 매 프레임 그대로 하되, UI로 전달하는 빈도만 줄인다."""
        if self._last_fps_emit_s is not None and now - self._last_fps_emit_s < 1.0:
            return
        self._last_fps_emit_s = now
        self.fps_stats_updated.emit(self._received_fps, self._processed_fps)

    def _current_frame_skip_n(self) -> int:
        """지금 몇 프레임에 한 번씩 검출/상태기계를 처리해야 하는지 계산한다 - 평상시
        (측정된 평균 처리 시간이 카메라 프레임 주기보다 짧으면)는 1(매 프레임 처리)을
        반환해 정밀도를 낮추지 않는다. 처리 시간이 프레임 주기를 넘어서기 시작하면 그
        비율만큼(올림) 건너뛰되, DetectionSettings.adaptive_frame_skip_max(기본 10 -
        "10프레임당 1개는 처리한다"는 사용자 마지노선, 2026-09-16)를 넘겨 건너뛰지는
        않는다. 아직 처리 시간을 한 번도 측정 못했으면(_avg_processing_time_s=None) 첫
        측정까지는 매 프레임 처리한다."""
        if self._avg_processing_time_s is None:
            return 1
        frame_rate_fps = self.settings.camera.frame_rate_fps
        if frame_rate_fps <= 0:
            return 1
        frame_period_s = 1.0 / frame_rate_fps
        ideal_skip = math.ceil(self._avg_processing_time_s / frame_period_s)
        return max(1, min(self.settings.detection.adaptive_frame_skip_max, ideal_skip))

    @staticmethod
    def _log_frame_outcome(result: DetectionResult, candidates: list[DetectionResult], out_of_range: bool) -> None:
        """이번 프레임에서 레드닷을 검출/미검출한 이유를 콘솔에 남긴다 - 실제 시험 중 레드닷을
        못 찾는 상황이 발생해서(사용자 요청, 2026-09-15), RedDotDetector 자체의 후보별 제외
        사유([detect] 로그, red_dot_detector.py 참고)에 이어 BlobTracker/범위 밖 폴백까지
        포함한 최종 판단 이유를 보여준다. settings.detection.debug_logging이 켜져 있을 때만
        호출된다."""
        if out_of_range:
            print(f"[detect] 결과: 범위 밖에서 발견 - center={result.center_px} (수동 조정 필요)")
        elif result.found:
            print(f"[detect] 결과: 검출 성공 center={result.center_px} area={result.area_px2:.0f}")
        elif candidates:
            print(f"[detect] 결과: 후보 {len(candidates)}개 있었지만 트래커가 거부(이전 위치에서 너무 멀리 이동)")
        else:
            print("[detect] 결과: 이번 프레임에서 후보 없음 (위 [detect] 제외 로그 참고)")

    @staticmethod
    def _log_stage2_state(state: Stage2LiveState) -> None:
        """진행 중인 방향이 화면에서 잘 파악이 안 된다는 지적(2026-09-16)에 따라, 매 프레임
        현재 상태기계가 인식하고 있는 방향/위치를 콘솔에 남긴다 - "검출 로그 출력" 체크박스
        (settings.detection.debug_logging)에 연동."""
        if state.current_direction is None:
            print(f"[stage2] 진행 중인 방향: 없음 (대기 중) - 현재=({state.current_x_moa:.2f}, {state.current_y_moa:.2f})")
            return
        print(
            f"[stage2] 진행 중인 방향: {state.current_direction.value} "
            f"시작={state.baseline_moa} 현재=({state.current_x_moa:.2f}, {state.current_y_moa:.2f}) "
            f"최대도달={state.max_primary_reached_moa:.2f} 최대편차={state.max_abs_cross_moa:.2f} "
            f"주축상대값={state.last_primary_moa:.2f}"
        )
