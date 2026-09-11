"""카메라 서비스 인터페이스.

실제 하드웨어(IDS peak SDK)와 개발/테스트용 Mock 구현을 동일한 인터페이스로 다루기 위한 추상화.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

import numpy as np

FrameCallback = Callable[[np.ndarray], None]


@dataclass
class CameraInfo:
    device_id: str
    model_name: str = ""
    serial_number: str = ""


class ICameraService(ABC):
    """카메라 연결/프레임 수신을 담당하는 인터페이스.

    구현체: IdsPeakCameraService(실기), MockCameraService(개발/테스트).
    """

    @abstractmethod
    def open(self) -> CameraInfo:
        """카메라를 열고 정보를 반환한다. 이미 열려 있으면 그대로 반환."""

    @abstractmethod
    def start(self, on_frame: FrameCallback) -> None:
        """백그라운드에서 프레임 캡처를 시작하고, 매 프레임마다 on_frame(BGR ndarray)을 호출한다."""

    @abstractmethod
    def stop(self) -> None:
        """프레임 캡처를 중단한다(카메라 핸들은 유지, close()에서 해제)."""

    @abstractmethod
    def close(self) -> None:
        """카메라 핸들을 해제한다."""

    @abstractmethod
    def apply_settings(self, settings: "CameraSettings") -> None:  # noqa: F821 - forward ref, see settings.py
        """카메라 파라미터(노출/게인/화이트밸런스 등)를 적용한다.

        구현체는 '자동기능 끄기 -> 수동값 설정' 순서를 지켜야 한다(SDK가 Auto On 상태에서
        수동값 설정을 무시/거부하는 경우가 있음 - docs 계획 참고).
        """

    @property
    @abstractmethod
    def is_running(self) -> bool:
        ...
