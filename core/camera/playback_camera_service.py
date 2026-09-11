"""실기 없이 캘리브레이션/검출 로직을 검증하기 위한 재생(playback) 카메라.

두 가지 소스를 지원한다:
- 정지 이미지(조리개 열림 상태의 밝은 그리드 캡처 등): 동일 프레임을 fps로 반복 송출.
  -> GridAutoDetector/PixelAngleCalibration 검증에 사용.
- 녹화 영상(실제 시험 영상): 파일을 순차 재생, loop=True면 끝나면 처음부터 반복.
  -> RedDotDetector/BlobTracker/TravelTestStateMachine 검증(시뮬레이션 모드)에 사용.

ICameraService와 동일한 인터페이스이므로 UI/파이프라인 코드를 바꾸지 않고도
Mock/실기(IDS peak)/재생 소스를 서로 교체해서 쓸 수 있다.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np

from core.camera.camera_service import CameraInfo, FrameCallback, ICameraService

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv"}


class PlaybackCameraService(ICameraService):
    def __init__(self, source_path: str | Path, loop: bool = True, fallback_fps: float = 15.0) -> None:
        self.source_path = Path(source_path)
        self.loop = loop
        self.fallback_fps = fallback_fps

        suffix = self.source_path.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            self._mode = "image"
        elif suffix in _VIDEO_EXTENSIONS:
            self._mode = "video"
        else:
            raise ValueError(f"지원하지 않는 파일 형식: {suffix} (이미지: {_IMAGE_EXTENSIONS}, 영상: {_VIDEO_EXTENSIONS})")

        self._still_frame: np.ndarray | None = None
        self._cap: cv2.VideoCapture | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def open(self) -> CameraInfo:
        if not self.source_path.exists():
            raise FileNotFoundError(f"재생 소스를 찾을 수 없습니다: {self.source_path}")

        if self._mode == "image":
            frame = cv2.imread(str(self.source_path))
            if frame is None:
                raise RuntimeError(f"이미지를 읽을 수 없습니다: {self.source_path}")
            self._still_frame = frame
        else:
            self._cap = cv2.VideoCapture(str(self.source_path))
            if not self._cap.isOpened():
                raise RuntimeError(f"영상을 열 수 없습니다: {self.source_path}")

        return CameraInfo(
            device_id=f"PLAYBACK:{self.source_path.name}",
            model_name=f"Playback({self._mode})",
            serial_number=self.source_path.name,
        )

    def start(self, on_frame: FrameCallback) -> None:
        if self._running:
            return
        self._running = True

        if self._mode == "image":
            self._thread = threading.Thread(target=self._loop_image, args=(on_frame,), daemon=True)
        else:
            self._thread = threading.Thread(target=self._loop_video, args=(on_frame,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def close(self) -> None:
        self.stop()
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._still_frame = None

    def apply_settings(self, settings) -> None:  # noqa: ANN001 - 재생 소스는 카메라 파라미터 없음
        return None

    @property
    def is_running(self) -> bool:
        return self._running

    # ---- 내부 재생 루프 ----
    def _loop_image(self, on_frame: FrameCallback) -> None:
        period = 1.0 / self.fallback_fps
        while self._running and self._still_frame is not None:
            on_frame(self._still_frame.copy())
            time.sleep(period)

    def _loop_video(self, on_frame: FrameCallback) -> None:
        assert self._cap is not None
        fps = self._cap.get(cv2.CAP_PROP_FPS) or self.fallback_fps
        period = 1.0 / fps if fps > 0 else 1.0 / self.fallback_fps

        while self._running:
            ok, frame = self._cap.read()
            if not ok:
                if self.loop:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                self._running = False
                break
            on_frame(frame)
            time.sleep(period)
