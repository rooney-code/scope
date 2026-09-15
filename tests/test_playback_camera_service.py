import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from core.camera.playback_camera_service import PlaybackCameraService
from core.calibration.grid_auto_detector import GridAutoDetector
from core.vision.red_dot_detector import RedDotDetector
from core.vision.blob_tracker import BlobTracker
from core.config.settings import DetectionSettings


def _make_bright_grid_image(width=800, height=600, origin=(400, 300), px_per_unit=6.0):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 1] = 90
    ox, oy = origin
    cv2.line(frame, (0, oy), (width, oy), (220, 220, 220), 2)
    cv2.line(frame, (ox, 0), (ox, height), (220, 220, 220), 2)
    for m in range(-30, 31, 10):
        if m == 0:
            continue
        tx = int(ox + m * px_per_unit)
        ty = int(oy - m * px_per_unit)
        cv2.line(frame, (tx, oy - 6), (tx, oy + 6), (220, 220, 220), 2)
        cv2.line(frame, (ox - 6, ty), (ox + 6, ty), (220, 220, 220), 2)
    return frame


def test_playback_still_image_feeds_grid_auto_detector():
    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "bright_grid.png"
        cv2.imwrite(str(img_path), _make_bright_grid_image())

        cam = PlaybackCameraService(img_path, fallback_fps=30.0)
        info = cam.open()
        assert "PLAYBACK" in info.device_id

        frames = []
        cam.start(lambda f: frames.append(f))
        time.sleep(0.15)
        cam.stop()
        cam.close()

        assert len(frames) >= 2  # 반복 송출 확인

        detector = GridAutoDetector(min_line_length_ratio=0.3)
        result = detector.detect(frames[0])
        assert result.found
        ox, oy = result.origin_px
        assert abs(ox - 400) < 5
        assert abs(oy - 300) < 5


def _make_test_video(path: Path, width=320, height=240, n_frames=20, fps=20.0):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
    for i in range(n_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[..., 1] = 60
        x = 50 + i * 5
        cv2.circle(frame, (x, 120), 8, (40, 160, 230), -1)
        writer.write(frame)
    writer.release()


def test_playback_folder_cycles_through_images_in_filename_order():
    """실 장비 없이 여러 장의 실측 이미지를 순서대로 넘겨가며 확인하고 싶다는 요청
    (2026-09-14) - 폴더를 넘기면 파일명 순으로 seconds_per_image 간격마다 다음 이미지로
    넘어가야 하고, 끝까지 가면 처음부터 반복해야 한다."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        # 파일명 순서와 다른 순서로 픽셀 마커를 심어서, 재생 순서가 알파벳순(파일명순)을
        # 따르는지(디렉터리 생성 순서 등 다른 기준이 아니라) 검증한다.
        markers = {"b_second.png": 2, "a_first.png": 1, "c_third.png": 3}
        for name, marker in markers.items():
            frame = np.zeros((20, 20, 3), dtype=np.uint8)
            frame[0, 0] = [marker, marker, marker]
            cv2.imwrite(str(folder / name), frame)

        cam = PlaybackCameraService(folder, seconds_per_image=0.05)
        info = cam.open()
        assert "PLAYBACK" in info.device_id

        received_markers = []
        cam.start(lambda f: received_markers.append(int(f[0, 0, 0])))
        time.sleep(0.22)  # 3장을 한 바퀴 돌고 다시 처음으로 넘어갈 정도의 시간
        cam.stop()
        cam.close()

        assert received_markers[:3] == [1, 2, 3]  # a_first -> b_second -> c_third 순서
        assert received_markers[3] == 1  # 끝까지 간 뒤 처음(a_first)으로 반복


def test_playback_video_feeds_red_dot_detector_and_tracker():
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "test_run.mp4"
        _make_test_video(video_path, n_frames=15)

        cam = PlaybackCameraService(video_path, loop=False)
        cam.open()

        detector = RedDotDetector(DetectionSettings())
        tracker = BlobTracker(max_jump_px=30)
        detections = []

        def on_frame(frame):
            result = tracker.select(detector.detect(frame))
            if result.found:
                detections.append(result.center_px)

        cam.start(on_frame)
        # 비디오가 끝나면 loop=False라 자동으로 멈춤 (is_running False)
        for _ in range(50):
            if not cam.is_running:
                break
            time.sleep(0.05)
        cam.close()

        assert len(detections) >= 10  # 대부분의 프레임에서 검출되어야 함
        xs = [p[0] for p in detections]
        assert xs[-1] > xs[0]  # 시간에 따라 우측으로 이동하는 궤적을 따라갔는지 확인


def test_playback_video_exposes_total_frame_count():
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "test_run.mp4"
        _make_test_video(video_path, n_frames=20)

        cam = PlaybackCameraService(video_path, loop=False, fallback_fps=50.0)
        cam.open()
        cam.start(lambda f: None)
        for _ in range(40):
            if cam.total_frame_count > 0:
                break
            time.sleep(0.02)
        cam.stop()
        cam.close()

        assert cam.total_frame_count == 20


def test_playback_video_pause_holds_last_frame_and_keeps_calling_on_frame():
    """일시정지 중에도 사용자가 실제 카메라를 다루듯 시험 절차를 계속 진행할 수 있어야
    한다 - InspectionViewModel._on_frame이 계속 호출돼 StabilityDetector 같은 시간 기반
    판정이 재생 중과 동일하게 동작해야 하므로, 마지막 프레임을 같은 간격으로 계속
    재전송해야 한다(사용자 요청, 2026-09-15)."""
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "test_run.mp4"
        _make_test_video(video_path, n_frames=20, fps=50.0)

        cam = PlaybackCameraService(video_path, loop=False, fallback_fps=50.0)
        cam.open()
        frames = []
        cam.start(lambda f: frames.append(f))

        # 몇 프레임 진행된 뒤 일시정지
        for _ in range(40):
            if cam.current_frame_index >= 3:
                break
            time.sleep(0.02)
        cam.pause()
        assert cam.is_paused
        frame_index_at_pause = cam.current_frame_index
        count_at_pause = len(frames)

        time.sleep(0.2)  # 일시정지 상태로 한동안 대기 - 그래도 계속 재전송돼야 함
        cam.stop()
        cam.close()

        assert cam.current_frame_index == frame_index_at_pause  # 새 프레임을 읽지 않음
        assert len(frames) > count_at_pause  # 그래도 on_frame은 계속 호출됨
        # 일시정지 중 재전송된 프레임은 전부 마지막 프레임과 동일해야 한다.
        last_frame = frames[count_at_pause - 1]
        for f in frames[count_at_pause:]:
            assert np.array_equal(f, last_frame)


def test_playback_video_resume_continues_advancing():
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "test_run.mp4"
        _make_test_video(video_path, n_frames=20, fps=50.0)

        cam = PlaybackCameraService(video_path, loop=False, fallback_fps=50.0)
        cam.open()
        cam.start(lambda f: None)

        for _ in range(40):
            if cam.current_frame_index >= 3:
                break
            time.sleep(0.02)
        cam.pause()
        time.sleep(0.1)
        frame_index_while_paused = cam.current_frame_index

        cam.resume()
        assert not cam.is_paused
        for _ in range(60):
            if cam.current_frame_index > frame_index_while_paused:
                break
            time.sleep(0.02)
        cam.stop()
        cam.close()

        assert cam.current_frame_index > frame_index_while_paused


def test_playback_video_frame_skip_interval_skips_frames_but_still_shows_and_advances():
    """처리 속도가 영상 fps를 못 따라갈 때 실제 장비에서 벌어질 프레임 드롭을
    시뮬레이션하기 위한 기능 - 건너뛴 프레임은 화면(on_frame)에도 노출되면 안 된다
    (사용자 요청, 2026-09-15: 분석 프레임 = 화면에 보이는 프레임)."""
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "test_run.mp4"
        _make_test_video(video_path, n_frames=20, fps=50.0)

        cam = PlaybackCameraService(video_path, loop=False, fallback_fps=50.0)
        cam.frame_skip_interval = 5
        cam.open()
        frames = []
        cam.start(lambda f: frames.append(f))
        for _ in range(100):
            if not cam.is_running:
                break
            time.sleep(0.02)
        cam.close()

        # 20프레임을 5개 단위로 건너뛰므로 실제로 화면/분석에 쓰이는 건 4장 정도여야 한다.
        assert 3 <= len(frames) <= 5
        assert cam.current_frame_index >= 15


def test_playback_video_seek_jumps_to_frame_and_emits_immediately():
    """재생/일시정지 버튼만으로는 특정 지점(예: 35MOA 근처)을 찾아가기 번거롭다는 요청
    (2026-09-15)에 따른 프로그레스바 탐색 기능 - seek()는 일시정지 여부와 무관하게 즉시
    그 프레임을 읽어 on_frame으로 내보내야 한다(슬라이더를 움직이면 바로 결과가 보여야
    자연스러움)."""
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "test_run.mp4"
        _make_test_video(video_path, n_frames=20, fps=50.0)

        cam = PlaybackCameraService(video_path, loop=False, fallback_fps=50.0)
        cam.open()
        frames = []
        cam.start(lambda f: frames.append(f))
        for _ in range(40):
            if cam.total_frame_count > 0:
                break
            time.sleep(0.02)
        cam.pause()
        time.sleep(0.05)  # 일시정지 재전송 루프가 안정된 뒤에 seek (타이밍 레이스 방지)

        count_before_seek = len(frames)
        cam.seek(15)
        # 일시정지 상태이므로 이후 재전송되는 프레임도 seek로 이동한 프레임이어야 한다.
        time.sleep(0.1)
        cam.stop()
        cam.close()

        # current_frame_index는 _loop_video의 기존 관례와 동일하게 "읽은 뒤"의
        # POS_FRAMES(다음에 읽을 위치) 값이라 seek(15)면 16이 된다(0번째 프레임을 읽으면
        # 1이 되는 것과 동일한 1-based "지금까지 읽은 프레임 수" 의미 - 기존 재생 루프의
        # current_frame_index 계산과 일관성 유지).
        assert cam.current_frame_index == 16
        assert len(frames) > count_before_seek  # seek 직후 즉시 한 번 더 emit됨
        seeked_frame = frames[count_before_seek]
        assert np.array_equal(frames[-1], seeked_frame)


def test_playback_video_seek_clamps_out_of_range_index():
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "test_run.mp4"
        _make_test_video(video_path, n_frames=20, fps=50.0)

        cam = PlaybackCameraService(video_path, loop=False, fallback_fps=50.0)
        cam.open()
        cam.start(lambda f: None)
        for _ in range(40):
            if cam.total_frame_count > 0:
                break
            time.sleep(0.02)
        cam.pause()

        cam.seek(9999)
        total = cam.total_frame_count
        current_after_huge_seek = cam.current_frame_index

        cam.seek(-5)
        current_after_negative_seek = cam.current_frame_index

        cam.stop()
        cam.close()

        # 9999는 마지막 유효 프레임(total-1)으로 clamp된 뒤 읽히므로, 읽은 뒤의 위치
        # (current_frame_index 관례 - 위 테스트 참고)는 total_frame_count와 같다.
        assert current_after_huge_seek == total
        assert current_after_negative_seek == 1  # -5는 0으로 clamp -> 읽은 뒤 위치는 1
