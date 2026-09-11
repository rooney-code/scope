"""하드웨어 없이 개발/테스트하기 위한 가상 카메라.

보어사이터 그리드(초록 배경 + 십자선 + tick)와 레드닷을 합성한 프레임을 생성한다.
`set_dot_position_px()`로 외부(테스트/데모 UI)에서 레드닷 위치를 제어할 수 있어
RedDotDetector, PixelAngleCalibration, TravelTestStateMachine을 실기 없이 검증 가능하게 한다.
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from core.camera.camera_service import CameraInfo, FrameCallback, ICameraService


class MockCameraService(ICameraService):
    def __init__(self, width: int = 1280, height: int = 960, fps: float = 30.0) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._origin = (width // 2, height // 2)
        self._px_per_moa = 6.0  # 가상 카메라의 임의 스케일(실기 캘리브레이션 대상과 별개)
        self._dot_px = list(self._origin)
        self._dot_elliptical = False  # 이동 범위 끝 왜곡 시뮬레이션 토글

    # ---- 테스트/데모 제어용 API (실 하드웨어에는 없음) ----
    def set_dot_position_moa(self, x_moa: float, y_moa: float) -> None:
        with self._lock:
            self._dot_px[0] = self._origin[0] + x_moa * self._px_per_moa
            # 화면 Y축은 아래로 증가하므로 위쪽(+Y, up)을 화면 위로 그리기 위해 반전
            self._dot_px[1] = self._origin[1] - y_moa * self._px_per_moa
            self._dot_elliptical = abs(x_moa) > 30 or abs(y_moa) > 30

    def px_per_moa(self) -> float:
        return self._px_per_moa

    def origin_px(self) -> tuple[int, int]:
        return self._origin

    # ---- ICameraService ----
    def open(self) -> CameraInfo:
        return CameraInfo(device_id="MOCK-0001", model_name="MockCamera", serial_number="0000000")

    def start(self, on_frame: FrameCallback) -> None:
        if self._running:
            return
        self._running = True

        def _loop() -> None:
            period = 1.0 / self._fps
            while self._running:
                frame = self._render_frame()
                on_frame(frame)
                time.sleep(period)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def close(self) -> None:
        self.stop()

    def apply_settings(self, settings) -> None:  # noqa: ANN001 - Mock ignores real camera params
        return None

    @property
    def is_running(self) -> bool:
        return self._running

    # ---- 내부 렌더링 ----
    def _render_frame(self) -> np.ndarray:
        h, w = self._height, self._width
        frame = np.zeros((h, w, 3), dtype=np.uint8)

        # 초록 배경(비네팅 느낌으로 중심이 밝게)
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = self._origin
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        max_dist = np.sqrt(cx**2 + cy**2)
        g = np.clip(80 - (dist / max_dist) * 80, 0, 255).astype(np.uint8)
        frame[..., 1] = g  # G channel

        # 십자선
        cv2.line(frame, (0, cy), (w, cy), (30, 60, 30), 1)
        cv2.line(frame, (cx, 0), (cx, h), (30, 60, 30), 1)

        # tick (10 단위 MOA 간격)
        for m in range(-35, 36, 10):
            if m == 0:
                continue
            tx = int(cx + m * self._px_per_moa)
            ty = int(cy - m * self._px_per_moa)
            cv2.line(frame, (tx, cy - 6), (tx, cy + 6), (30, 60, 30), 1)
            cv2.line(frame, (cx - 6, ty), (cx + 6, ty), (30, 60, 30), 1)

        # 레드닷 (호박색, 중심 밝고 발광)
        dot = (int(self._dot_px[0]), int(self._dot_px[1]))
        # 단일 블롭으로 단순화 (별도의 외곽 링을 그리면 특정 위치에서 내부 원과 분리된
        # 컨투어로 잡혀 다중 블롭 아티팩트가 생기는 경우가 있어 제거함 - Mock 전용 이슈,
        # 실제 검출 튜닝은 실기 영상으로 진행)
        if self._dot_elliptical:
            cv2.ellipse(frame, dot, (10, 6), 0, 0, 360, (40, 160, 230), -1)
        else:
            cv2.circle(frame, dot, 8, (40, 160, 230), -1)

        return frame
