"""실기 없이 캘리브레이션/검출 로직을 검증하기 위한 재생(playback) 카메라.

세 가지 소스를 지원한다:
- 정지 이미지(조리개 열림 상태의 밝은 그리드 캡처 등): 동일 프레임을 fps로 반복 송출.
  -> GridAutoDetector/PixelAngleCalibration 검증에 사용.
- 녹화 영상(실제 시험 영상): 파일을 순차 재생, loop=True면 끝나면 처음부터 반복.
  -> RedDotDetector/BlobTracker/TravelTestStateMachine 검증(시뮬레이션 모드)에 사용.
- 이미지 폴더: 폴더 안의 이미지들을 파일명 순으로 seconds_per_image 간격으로 한 장씩
  넘겨가며 재생, 끝까지 가면 처음부터 반복. 실제 장비 없이 여러 장의 실측 캡처
  이미지(예: tests/test_images/real_footage_.../*.jpg)를 순서대로 눈으로 확인하며
  검증하고 싶을 때 사용(프로그램을 여러 번 재시작하지 않아도 됨) - 사용자 요청,
  2026-09-14.

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
    def __init__(
        self,
        source_path: str | Path,
        loop: bool = True,
        fallback_fps: float = 15.0,
        seconds_per_image: float = 3.0,
    ) -> None:
        self.source_path = Path(source_path)
        self.loop = loop
        self.fallback_fps = fallback_fps
        self.seconds_per_image = seconds_per_image

        self._image_files: list[Path] = []
        if self.source_path.is_dir():
            self._mode = "folder"
            # 파일명 순(보통 촬영 순서와 일치)으로 정렬 - 대소문자 구분 없이.
            self._image_files = sorted(
                (p for p in self.source_path.iterdir() if p.suffix.lower() in _IMAGE_EXTENSIONS),
                key=lambda p: p.name.lower(),
            )
        else:
            suffix = self.source_path.suffix.lower()
            if suffix in _IMAGE_EXTENSIONS:
                self._mode = "image"
            elif suffix in _VIDEO_EXTENSIONS:
                self._mode = "video"
            else:
                raise ValueError(f"지원하지 않는 파일 형식: {suffix} (이미지: {_IMAGE_EXTENSIONS}, 영상: {_VIDEO_EXTENSIONS})")

        # 폴더 모드는 서로 무관한 이미지들을 순서대로 보여주는 것이라(연속된 움직임이
        # 아님), InspectionViewModel이 매 프레임 BlobTracker를 리셋해서 "이전 프레임과
        # 가까운 위치만 채택" 게이팅이 걸리지 않게 해야 한다 - 안 그러면 레드닷 위치가
        # 이미지마다 크게 점프해서 첫 이미지 이후로는 전부 검출 실패로 처리된다(실측으로
        # 확인, 2026-09-14).
        self.reset_tracker_each_frame = self._mode == "folder"

        self._still_frame: np.ndarray | None = None
        self._cap: cv2.VideoCapture | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        # 폴더 모드에서 지금 화면에 나온 이미지 파일 - 콘솔 로그 외에 프로그램적으로도
        # "지금 몇 번째/무슨 파일인지" 확인할 수 있게 공개 속성으로 둔다.
        self.current_file: Path | None = None

    def open(self) -> CameraInfo:
        if not self.source_path.exists():
            raise FileNotFoundError(f"재생 소스를 찾을 수 없습니다: {self.source_path}")

        if self._mode == "folder":
            if not self._image_files:
                raise RuntimeError(f"폴더에 이미지가 없습니다: {self.source_path}")
        elif self._mode == "image":
            self._still_frame = self._read_image(self.source_path)
        else:
            self._cap = cv2.VideoCapture(_video_capture_path(str(self.source_path)))
            if not self._cap.isOpened():
                raise RuntimeError(f"영상을 열 수 없습니다: {self.source_path}")

        return CameraInfo(
            device_id=f"PLAYBACK:{self.source_path.name}",
            model_name=f"Playback({self._mode})",
            serial_number=self.source_path.name,
        )

    @staticmethod
    def _read_image(path: Path) -> np.ndarray:
        # cv2.imread(str(path))는 Windows에서 비-ASCII(한글 등) 경로를 OS 코드페이지로
        # 잘못 처리해 파일을 못 찾는 버그가 있다(예: "O:\10. 프로젝트\scope\..." 같은
        # 경로에서 재현됨, 실측 확인). np.fromfile은 Python 자체 파일 I/O라 유니코드
        # 경로를 문제없이 열 수 있으므로, 바이트로 읽은 뒤 cv2.imdecode로 디코딩한다.
        file_bytes = np.fromfile(str(path), dtype=np.uint8)
        frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(f"이미지를 읽을 수 없습니다: {path}")
        return frame

    def start(self, on_frame: FrameCallback) -> None:
        if self._running:
            return
        self._running = True

        if self._mode == "folder":
            self._thread = threading.Thread(target=self._loop_folder, args=(on_frame,), daemon=True)
        elif self._mode == "image":
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
    def _loop_folder(self, on_frame: FrameCallback) -> None:
        """폴더 안 이미지를 파일명 순으로 seconds_per_image 간격으로 한 장씩 내보낸다.
        같은 프레임을 반복 송출하는 대신 매번 다음 이미지로 넘어간다는 점이 _loop_image와
        다르다 - 끝까지 가면 처음으로 돌아간다(loop=True 기본). 매 순환마다 다시 디코딩해서
        메모리에 전체 이미지를 올려두지 않는다(폴더에 이미지가 많을 수 있어서)."""
        index = 0
        while self._running:
            path = self._image_files[index]
            self.current_file = path
            # 지금 화면에 나온 게 어느 파일인지 콘솔에 남긴다 - 문제(오검출 등)를 발견했을
            # 때 어떤 파일에서 났는지 바로 기록할 수 있게(사용자 요청, 2026-09-14).
            print(f"[playback] ({index + 1}/{len(self._image_files)}) {path.name}")
            try:
                frame = self._read_image(path)
            except Exception as exc:  # noqa: BLE001 - 이미지 하나가 깨져 있어도 나머지는 계속 진행
                print(f"[playback] 이미지 읽기 실패, 건너뜀: {path} ({exc})")
            else:
                on_frame(frame)
            index += 1
            if index >= len(self._image_files):
                if not self.loop:
                    self._running = False
                    break
                index = 0
            time.sleep(self.seconds_per_image)

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
