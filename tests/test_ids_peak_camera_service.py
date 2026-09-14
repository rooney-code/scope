"""IdsPeakCameraService의 화이트밸런스 R/G/B 게인(호스트 측) 로직 검증.

실제 카메라(IdsPeakCameraService.open())는 필요 없지만, ids_peak_ipl 네이티브 라이브러리
자체는 필요하다(Gain/Image 클래스가 그 안에 있음) - SDK가 설치되지 않은 환경(대부분의 개발
PC)에서는 이 테스트 전체를 건너뛴다(pytest.importorskip). docs/detection_notes.md 17차 참고 -
합성 Bayer 패턴 이미지로 R 게인만 다르게 줬을 때 R 채널만 바뀌는지 직접 검증한다.
"""
from __future__ import annotations

import numpy as np
import pytest

ipl = pytest.importorskip("ids_peak_ipl.ids_peak_ipl")

from core.camera.ids_peak_camera_service import IdsPeakCameraService  # noqa: E402
from core.config.settings import CameraSettings  # noqa: E402


def _make_bayer_rg8_image(width: int = 4, height: int = 4):
    """R=50, G=60, B=70인 2x2 BayerRG 패턴을 반복한 합성 이미지."""
    buf = np.zeros((height, width), dtype=np.uint8)
    buf[0::2, 0::2] = 50  # R
    buf[0::2, 1::2] = 60  # G
    buf[1::2, 0::2] = 60  # G
    buf[1::2, 1::2] = 70  # B
    return ipl.Image.CreateFromSizeAndPythonBuffer(ipl.PixelFormatName_BayerRG8, buf, width, height)


def test_update_white_balance_gain_sets_values_from_settings():
    service = IdsPeakCameraService()
    service._ipl = ipl
    settings = CameraSettings(wb_gain_r=1.5, wb_gain_g=1.2, wb_gain_b=1.0)

    service._update_white_balance_gain(settings)

    assert service._wb_gain is not None
    assert service._wb_gain.RedGainValue() == pytest.approx(1.5)
    assert service._wb_gain.GreenGainValue() == pytest.approx(1.2)
    assert service._wb_gain.BlueGainValue() == pytest.approx(1.0)


def test_update_white_balance_gain_clamps_out_of_range_value_instead_of_disabling():
    """ids_peak_ipl.Gain의 실제 유효 범위는 1.0~8.0(실측 확인, docs/detection_notes.md 17차
    참고) - 1.0 미만 값(예: 0.8, "덜 파랗게" 의도)을 그대로 넘기면 SDK가 예외를 낸다. 값
    하나가 범위를 벗어났다고 화이트밸런스 전체를 꺼버리지 않고, 그 채널만 유효 범위로
    clamp해서 나머지 채널은 정상 적용되어야 한다."""
    service = IdsPeakCameraService()
    service._ipl = ipl
    settings = CameraSettings(wb_gain_r=1.5, wb_gain_g=1.0, wb_gain_b=0.8)  # blue가 범위 밖

    service._update_white_balance_gain(settings)

    assert service._wb_gain is not None  # 전체가 꺼지지 않음
    assert service._wb_gain.RedGainValue() == pytest.approx(1.5)
    assert service._wb_gain.BlueGainValue() == pytest.approx(1.0)  # 최소값으로 clamp됨


def test_apply_white_balance_gain_changes_only_red_channel():
    """R 게인만 2배로 주면 Bayer 패턴에서 R 위치 픽셀만 2배가 되고 G/B는 그대로여야 한다
    (실측으로 확인된 ids_peak_ipl.Gain의 실제 동작 - docs/detection_notes.md 17차 참고)."""
    service = IdsPeakCameraService()
    service._ipl = ipl
    service._update_white_balance_gain(CameraSettings(wb_gain_r=2.0, wb_gain_g=1.0, wb_gain_b=1.0))

    image = _make_bayer_rg8_image()
    service._apply_white_balance_gain(image)

    result = image.get_numpy_2D()
    assert result[0, 0] == 100  # R: 50 * 2.0
    assert result[0, 1] == 60  # G: 60 * 1.0 (변화 없음)
    assert result[1, 0] == 60  # G: 60 * 1.0 (변화 없음)
    assert result[1, 1] == 70  # B: 70 * 1.0 (변화 없음)


def test_apply_white_balance_gain_skips_unsupported_pixel_format():
    """이미 BGR8로 변환된 이미지에는 Gain을 적용할 수 없다(ids_peak_ipl 자체 제약) -
    IsPixelFormatSupported()가 False를 반환하므로 조용히 건너뛰어야 한다(예외 없이)."""
    service = IdsPeakCameraService()
    service._ipl = ipl
    service._update_white_balance_gain(CameraSettings(wb_gain_r=2.0, wb_gain_g=1.0, wb_gain_b=1.0))

    bgr_image = _make_bayer_rg8_image().ConvertTo(ipl.PixelFormatName_BGR8)
    service._apply_white_balance_gain(bgr_image)  # 예외 없이 그냥 건너뜀


def test_apply_white_balance_gain_noop_when_gain_not_configured():
    """apply_settings()를 아직 호출하지 않아 _wb_gain이 없으면 아무것도 하지 않아야 한다."""
    service = IdsPeakCameraService()
    service._ipl = ipl
    image = _make_bayer_rg8_image()
    before = image.get_numpy_2D().copy()

    service._apply_white_balance_gain(image)

    assert np.array_equal(image.get_numpy_2D(), before)
