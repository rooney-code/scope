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
