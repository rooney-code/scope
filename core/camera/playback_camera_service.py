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

import ctypes
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from core.camera.camera_service import CameraInfo, FrameCallback, ICameraService

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv"}


def _video_capture_path(path: str) -> str:
    """cv2.VideoCapture는 cv2.imread와 같은 부류로, Windows에서 비-ASCII(한글 등) 경로를
    못 여는 경우가 있다 - imread처럼 바이트를 직접 디코딩하는 우회(imdecode)가 스트리밍
    영상에는 없으므로, 대신 항상 ASCII인 8.3 짧은 경로명으로 바꿔서 연다. 짧은 경로를
    못 구하면(Windows가 아니거나 단축 경로가 비활성화된 드라이브) 원래 경로를 그대로
    쓴다 - 이 경우 ASCII 경로면 문제없이 열리고, 비-ASCII면 이전과 동일하게 실패한다."""
    if sys.platform != "win32":
        return path
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 260):
        return buf.value
    return path


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
            # cv2.imread(str(path))는 Windows에서 비-ASCII(한글 등) 경로를 OS 코드페이지로
            # 잘못 처리해 파일을 못 찾는 버그가 있다(예: "O:\10. 프로젝트\scope\..." 같은
            # 경로에서 재현됨, 실측 확인). np.fromfile은 Python 자체 파일 I/O라 유니코드
            # 경로를 문제없이 열 수 있으므로, 바이트로 읽은 뒤 cv2.imdecode로 디코딩한다.
            file_bytes = np.fromfile(str(self.source_path), dtype=np.uint8)
            frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            if frame is None:
                raise RuntimeError(f"이미지를 읽을 수 없습니다: {self.source_path}")
            self._still_frame = frame
        else:
            self._cap = cv2.VideoCapture(_video_capture_path(str(self.source_path)))
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

    def read_settings(self, base):  # noqa: ANN001, ANN201 - 재생 소스는 실제 카메라 제약이 없음
        return base, set()

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
