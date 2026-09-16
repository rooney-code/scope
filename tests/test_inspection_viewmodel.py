import tempfile
from pathlib import Path

import numpy as np
import cv2
from PySide6.QtWidgets import QApplication
import pytest

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from core.calibration.pixel_angle_calibration import CalibrationProfile
from core.camera.mock_camera_service import MockCameraService
from core.camera.playback_camera_service import PlaybackCameraService
from core.config.settings import load_default_settings
from core.data.repository import InspectionRepository
from core.inspection.models import TravelDirection, Verdict
from tests.fake_mssql import FakeMssqlConnection


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_vm(repository=None) -> InspectionViewModel:
    vm = InspectionViewModel(MockCameraService(), load_default_settings(), repository=repository)
    vm.set_scope_id("SCOPE-VM-TEST")
    return vm


def _pass_direction(vm: InspectionViewModel, direction: TravelDirection) -> None:
    """상태기계를 직접 조작해 한 방향을 합격으로 완료시킨다 (카메라/검출 파이프라인 없이
    뷰모델의 finalize/retest 배선만 검증하는 용도)."""
    vm.state_machine.start_next_direction()
    vm.state_machine._max_primary_reached = 35.0
    vm.mark_far_point_reached()
    vm.mark_returned_to_origin()


def test_finalize_without_repository_just_locks_state_machine():
    vm = _make_vm(repository=None)
    vm.configure_directions([TravelDirection.UP])
    _pass_direction(vm, TravelDirection.UP)

    assert vm.is_ready_to_finalize
    overall = vm.finalize_inspection()

    assert overall == Verdict.PASS.value
    assert vm.state_machine.finalized
    assert not vm.is_ready_to_finalize


def test_finalize_with_repository_saves_full_session():
    conn = FakeMssqlConnection()
    repo = InspectionRepository(conn)
    vm = _make_vm(repository=repo)
    vm.configure_directions([TravelDirection.UP])
    _pass_direction(vm, TravelDirection.UP)

    vm.finalize_inspection()

    assert len(conn.sessions) == 1
    session_id = next(iter(conn.sessions))
    assert conn.sessions[session_id]["ScopeId"] == "SCOPE-VM-TEST"
    assert conn.sessions[session_id]["OverallVerdict"] == Verdict.PASS.value
    assert conn.sessions[session_id]["CompletedAt"] is not None
    assert len(conn.direction_results) == 1


def _make_dot_frame(center_px: tuple[int, int], size=(800, 800)) -> np.ndarray:
    """MockCameraService와 같은 색(BGR)의 레드닷 블롭을 그린 합성 프레임 - RedDotDetector의
    HSV 임계값에 이미 맞춰진 색이다."""
    frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    cv2.circle(frame, center_px, 10, (40, 160, 230), -1)
    return frame


def test_discontinuous_source_still_detects_dot_after_large_jump():
    """폴더 재생처럼 프레임끼리 연속성이 없는 소스(camera.reset_tracker_each_frame=True)는
    레드닷이 이전 프레임과 멀리 떨어진 위치에 있어도 매번 새로 검출해야 한다 -
    BlobTracker의 max_jump_px 게이팅 때문에 두 번째 프레임부터 검출 실패로 처리되던
    문제에 대한 회귀 테스트(실측으로 확인, 2026-09-14)."""
    vm = _make_vm()
    vm.camera.reset_tracker_each_frame = True

    detections = []
    vm.detection_ready.connect(lambda r: detections.append(r))

    vm._on_frame(_make_dot_frame((50, 50)))
    vm._on_frame(_make_dot_frame((700, 700)))  # 이전 위치에서 멀리 "점프"

    assert len(detections) == 2
    assert detections[0].found
    assert detections[1].found  # reset 덕분에 점프해도 검출돼야 함


def test_continuous_source_rejects_large_jump_as_before():
    """연속 소스(reset_tracker_each_frame=False, 기본값)는 기존처럼 이전 프레임과 너무
    멀리 떨어진 블롭을 거부해야 한다 - 위 회귀 테스트가 이 게이팅 자체를 없앤 게 아니라
    불연속 소스에서만 우회한다는 것을 함께 확인."""
    vm = _make_vm()
    assert getattr(vm.camera, "reset_tracker_each_frame", False) is False

    detections = []
    vm.detection_ready.connect(lambda r: detections.append(r))

    vm._on_frame(_make_dot_frame((50, 50)))
    vm._on_frame(_make_dot_frame((700, 700)))

    assert detections[0].found
    assert not detections[1].found  # 점프가 max_jump_px를 넘어 거부됨(기존 동작 유지)


def test_retest_direction_delegates_to_state_machine():
    vm = _make_vm()
    vm.configure_directions([TravelDirection.UP])
    vm.state_machine._max_primary_reached = 15.0  # 이동량 미달로 실패시킴
    vm.state_machine.start_next_direction()
    vm.mark_far_point_reached()

    assert vm.state_machine.direction_results[-1].verdict == Verdict.FAIL

    vm.retest_direction(TravelDirection.UP)

    assert vm.state_machine.direction_results == []
    assert TravelDirection.UP in vm.state_machine.direction_queue


def test_state_machine_stability_factory_uses_settings_stability():
    """settings.stability(StabilitySettings)는 예전엔 로드만 되고 실제로 아무 데도 연결
    안 된 죽은 설정이었다 - 이번에 TravelTestStateMachine에 연결했다(사용자 요청,
    2026-09-16)."""
    vm = _make_vm()
    detector = vm.state_machine._stability_factory()
    assert detector.window_size_samples == vm.settings.stability.window_size_samples
    assert detector.variance_threshold_moa2 == vm.settings.stability.variance_threshold_moa2
    # StabilitySettings는 초(sec) 단위(설정 화면에서 다루기 쉽게, 사용자 요청 2026-09-16)지만
    # StabilityDetector 내부는 ms 단위이므로 1000을 곱한 값과 비교한다.
    assert detector.min_stable_duration_ms == vm.settings.stability.min_stable_duration_s * 1000.0


def test_stage2_live_update_emitted_when_session_active_and_calibrated():
    """방향별 박스 버튼이 더 이상 필수 조작이 아니게 되면서, 지금 무슨 일이 일어나고
    있는지 화면으로 볼 수 있어야 한다는 요청(2026-09-16)에 따른 실시간 표시 시그널."""
    vm = _make_vm()
    vm.calibration.profile = CalibrationProfile(
        camera_id="test",
        origin_px_x=400,
        origin_px_y=400,
        px_per_moa_x=10.0,
        px_per_moa_y=10.0,
        scale_confirmed_x=True,
        scale_confirmed_y=True,
    )
    vm._session_active = True

    updates = []
    vm.stage2_live_update.connect(lambda s: updates.append(s))

    vm._on_frame(_make_dot_frame((400, 400)))  # 원점 근처, 아직 방향 임계값 안 넘음

    assert len(updates) == 1
    assert updates[0].current_direction is None


def test_feed_position_not_called_before_session_started():
    """"시험 시작"을 누르기 전(_session_active=False)에는 대기 baseline 추적이 시작되면
    안 된다(사용자 요청, 2026-09-16) - stage2_live_update조차 emit되지 않아야 한다."""
    vm = _make_vm()
    vm.calibration.profile = CalibrationProfile(
        camera_id="test",
        origin_px_x=400,
        origin_px_y=400,
        px_per_moa_x=10.0,
        px_per_moa_y=10.0,
        scale_confirmed_x=True,
        scale_confirmed_y=True,
    )
    assert vm._session_active is False

    updates = []
    vm.stage2_live_update.connect(lambda s: updates.append(s))

    vm._on_frame(_make_dot_frame((400, 400)))

    assert updates == []


def test_feed_position_still_runs_for_manually_started_direction_without_session():
    """"시험 시작"을 누르지 않고(스모크 테스트 스크립트처럼) 방향 버튼으로 바로
    시작해도(state_machine.start_direction()), 이미 진행 중인 방향의 이동량 추적은
    계속 동작해야 한다 - _session_active 게이트가 idle 대기 추적뿐 아니라 이미 시작된
    방향까지 막아버리는 회귀가 있었다(실측/스모크 테스트로 확인, 2026-09-16)."""
    vm = _make_vm()
    vm.calibration.profile = CalibrationProfile(
        camera_id="test",
        origin_px_x=400,
        origin_px_y=400,
        px_per_moa_x=10.0,
        px_per_moa_y=10.0,
        scale_confirmed_x=True,
        scale_confirmed_y=True,
    )
    assert vm._session_active is False
    vm.state_machine.start_direction(TravelDirection.UP)  # "시험 시작" 없이 방향만 직접 시작

    vm._on_frame(_make_dot_frame((400, 400)))  # baseline 캡처
    vm._on_frame(_make_dot_frame((400, 350)))  # 위로 이동(픽셀 y 감소 = MOA 상승)

    assert vm.state_machine.max_primary_reached_moa > 0  # feed_position이 실제로 동작함


def test_finalize_inspection_writes_txt_report(tmp_path, monkeypatch):
    """DB는 나중에(기능 검증 후) 별도로 다시 붙이기로 하고, 지금은 배포 대상 PC에 DB 설치
    없이도 결과를 남길 수 있게 텍스트로 저장한다(사용자 요청, 2026-09-16)."""
    import core.data.text_report as text_report

    monkeypatch.setattr(text_report, "_DEFAULT_REPORTS_DIR", tmp_path)

    vm = _make_vm(repository=None)
    vm.configure_directions([TravelDirection.UP])
    _pass_direction(vm, TravelDirection.UP)

    vm.finalize_inspection()

    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1
    assert vm.scope_id in files[0].name
    content = files[0].read_text(encoding="utf-8")
    assert "합격" in content or "불량" in content


def _make_test_video(path: Path, n_frames: int = 10, fps: float = 20.0) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (200, 150))
    for _ in range(n_frames):
        frame = np.zeros((150, 200, 3), dtype=np.uint8)
        cv2.circle(frame, (100, 75), 10, (40, 160, 230), -1)
        writer.write(frame)
    writer.release()


def test_load_simulation_video_swaps_camera_and_starts_paused():
    """실장비 없이 녹화 영상으로 시험 절차를 검증하는 기능 - 영상을 선택하면 지금 카메라가
    무엇이든(여기서는 MockCameraService) 멈추고 PlaybackCameraService로 교체되어야 하고,
    작업자가 준비될 때까지 자동으로 재생되면 안 되므로 일시정지 상태로 시작해야 한다
    (사용자 요청, 2026-09-15)."""
    vm = _make_vm()
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "sim.mp4"
        _make_test_video(video_path)

        vm.load_simulation_video(str(video_path))

        assert isinstance(vm.camera, PlaybackCameraService)
        assert vm.camera.is_paused
        vm.camera.stop()
        vm.camera.close()


class _SpyCamera:
    def __init__(self) -> None:
        self.pause_calls = 0
        self.resume_calls = 0
        self.seek_calls: list[int] = []

    def pause(self) -> None:
        self.pause_calls += 1

    def resume(self) -> None:
        self.resume_calls += 1

    def seek(self, frame_index: int) -> None:
        self.seek_calls.append(frame_index)


def test_play_pause_simulation_video_delegate_to_camera():
    vm = _make_vm()
    spy = _SpyCamera()
    vm.camera = spy

    vm.play_simulation_video()
    vm.pause_simulation_video()

    assert spy.resume_calls == 1
    assert spy.pause_calls == 1


def test_on_frame_passes_head_direction_hint_from_current_test_direction():
    """RedDotDetector가 코멧테일 머리/꼬리 밝기가 애매할 때 무게중심 대신 쓸 방향 힌트
    (사용자 제안, 2026-09-15) - 현재 시험 중인 방향(state_machine.current_direction)을
    화면 픽셀 방향으로 변환해 detect()에 넘겨야 한다."""
    vm = _make_vm()
    vm.state_machine.current_direction = TravelDirection.UP

    calls = []
    original_detect = vm.detector.detect

    def spy_detect(frame_bgr, roi_px=None, head_direction_hint_px=None):
        calls.append(head_direction_hint_px)
        return original_detect(frame_bgr, roi_px=roi_px, head_direction_hint_px=head_direction_hint_px)

    vm.detector.detect = spy_detect
    vm._on_frame(_make_dot_frame((50, 50)))

    assert calls[0] == (0.0, -1.0)  # 상(UP) -> 픽셀 -y


def test_debug_logging_off_by_default_prints_nothing_on_frame(capsys):
    vm = _make_vm()
    vm._on_frame(_make_dot_frame((50, 50)))
    assert capsys.readouterr().out == ""


def test_debug_logging_prints_detection_outcome_when_enabled(capsys):
    """레드닷을 못 찾는 상황을 실제 시험 중 진단하기 위한 로그(사용자 요청, 2026-09-15) -
    켜면 RedDotDetector의 [detect] 후보 로그에 이어 최종 판단([detect] 결과)까지 남아야
    한다."""
    vm = _make_vm()
    vm.settings.detection.debug_logging = True

    vm._on_frame(_make_dot_frame((50, 50)))

    out = capsys.readouterr().out
    assert "[detect] 결과: 검출 성공" in out


def test_seek_simulation_video_delegates_to_camera():
    """재생/일시정지 버튼만으로는 특정 지점을 찾아가기 번거롭다는 요청(2026-09-15)에
    따른 프로그레스바 탐색 기능."""
    vm = _make_vm()
    spy = _SpyCamera()
    vm.camera = spy

    vm.seek_simulation_video(42)

    assert spy.seek_calls == [42]


def test_avg_frame_processing_ms_starts_none_then_becomes_positive_after_frames():
    vm = _make_vm()
    assert vm.avg_frame_processing_ms is None

    vm._on_frame(_make_dot_frame((50, 50)))
    vm._on_frame(_make_dot_frame((60, 60)))

    assert vm.avg_frame_processing_ms is not None
    assert vm.avg_frame_processing_ms >= 0.0
