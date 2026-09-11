"""수신 프레임을 여러 구독자(UI 프리뷰, 검출 파이프라인)에 분배.

검출이 UI 스레드를 막지 않도록, 구독자 콜백은 각각 독립적으로 호출된다.
구독자 콜백 안에서 예외가 발생해도 다른 구독자와 캡처 루프에 영향이 없게 격리한다.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

FrameSubscriber = Callable[[np.ndarray], None]


class FrameBus:
    def __init__(self) -> None:
        self._subscribers: list[FrameSubscriber] = []

    def subscribe(self, callback: FrameSubscriber) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: FrameSubscriber) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def publish(self, frame: np.ndarray) -> None:
        for sub in list(self._subscribers):
            try:
                sub(frame)
            except Exception as exc:  # noqa: BLE001 - 구독자 오류가 캡처 루프를 죽이면 안 됨
                print(f"[frame_bus] 구독자 콜백 오류: {exc}")
