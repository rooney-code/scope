import cv2
import numpy as np

from core.calibration.grid_overlay import draw_coordinate_axes
from core.calibration.pixel_angle_calibration import CalibrationProfile

_OX, _OY = 300.0, 300.0
_PX_PER_MOA = 10.0


def _profile() -> CalibrationProfile:
    return CalibrationProfile(
        camera_id="CAM",
        origin_px_x=_OX,
        origin_px_y=_OY,
        px_per_moa_x=_PX_PER_MOA,
        px_per_moa_y=_PX_PER_MOA,
        scale_confirmed_x=True,
        scale_confirmed_y=True,
    )


def _tick_vertical_extent(frame: np.ndarray, x_px: int, oy_i: int) -> int:
    """주어진 열(x_px)에서 oy_i를 포함하는 연속된(칠해진) 구간의 길이 - 그 열에 그려진
    tick 눈금의 길이를 근사한다.

    측정 열이 원점과 가까우면(px_per_moa가 작을 때) 다른(수직) 축의 더 긴 tick이 가로로
    번져 같은 열에 멀리 떨어진 별개의 색칠 구간을 만들 수 있다 - 원점을 포함하는 연속
    구간만 봐야 그 조각들이 결과에 섞이지 않는다."""
    col = frame[:, x_px, 2]  # BGR의 R 채널
    colored = col > 0
    top = oy_i
    while top - 1 >= 0 and colored[top - 1]:
        top -= 1
    bottom = oy_i
    n = len(colored)
    while bottom + 1 < n and colored[bottom + 1]:
        bottom += 1
    return max(oy_i - top, bottom - oy_i)


def test_tick_length_tiers_1_5_10_moa():
    """1MOA 기본 눈금보다 5MOA 배수는 2배, 10MOA 배수는 4배 길게 그려야 한다 - 10/20/30
    단위가 1단위와 길이 차이가 적어 잘 안 보인다는 피드백에 대한 수정(2026-09-14)."""
    frame = np.zeros((600, 600, 3), dtype=np.uint8)
    profile = _profile()
    out = draw_coordinate_axes(
        frame, profile, tick_step_moa=1.0, label_step_moa=10.0, minor_tick_length_px=4, max_moa_range=12
    )
    oy_i = int(round(_OY))

    len_1 = _tick_vertical_extent(out, int(round(_OX + 1 * _PX_PER_MOA)), oy_i)
    len_5 = _tick_vertical_extent(out, int(round(_OX + 5 * _PX_PER_MOA)), oy_i)
    len_10 = _tick_vertical_extent(out, int(round(_OX + 10 * _PX_PER_MOA)), oy_i)

    assert len_1 < len_5 < len_10
    # 대략적인 비율 확인(라벨 텍스트가 같은 열까지 번지는 경우가 있어 완전히 정확한 배수는
    # 아닐 수 있으므로 넉넉한 허용치를 둠)
    assert len_5 >= len_1 * 1.5
    assert len_10 >= len_5 * 1.5


def test_x_axis_label_sign_controls_above_below_placement():
    """+x 라벨은 축 위, -x 라벨은 축 아래에 그려져야 한다 - 숫자가 눈금 선을 가려서 잘 안
    보인다는 피드백에 대한 수정(2026-09-14). tick_step_moa=10.0으로 length가 기본값(4,
    5/10배수가 아님)이 되게 해서, 라벨 위치를 구현과 동일한 공식으로 예측해 검증한다."""
    frame = np.zeros((600, 600, 3), dtype=np.uint8)
    profile = _profile()
    oy_i = int(round(_OY))
    length = 4  # tick_step_moa=10.0이면 i=±1이라 5/10배수 tier에 안 걸림(기본 길이 그대로)

    out = draw_coordinate_axes(frame, profile, tick_step_moa=10.0, label_step_moa=10.0, max_moa_range=10)

    font_scale = max(0.4, min(1.0, 600 / 2400))
    (_, text_h), _ = cv2.getTextSize("10", cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)

    # +x(m=10) 라벨: 구현 공식상 baseline=oy_i-length-4, 텍스트는 그 위로 text_h만큼 번짐
    above_row_range = slice(max(0, oy_i - length - 4 - text_h - 2), oy_i - length - 2)
    # -x(m=-10) 라벨: baseline=oy_i+length+4+text_h, 텍스트는 그 위(=oy_i 쪽)로 번짐
    below_row_range = slice(oy_i + length + 2, oy_i + length + 4 + text_h + 2)

    assert out[above_row_range, :, 2].any()  # +x 라벨(축 위)이 그려져 있어야 함
    assert not out[below_row_range, 400:500, 2].any()  # +x 근방(오른쪽)엔 축 아래 라벨이 없어야 함
    assert out[below_row_range, :, 2].any()  # -x 라벨(축 아래)이 그려져 있어야 함(같은 프레임, 반대쪽 x)
    assert not out[above_row_range, 100:200, 2].any()  # -x 근방(왼쪽)엔 축 위 라벨이 없어야 함


def test_y_axis_label_sign_controls_left_right_placement():
    """+y 라벨은 축 오른쪽, -y 라벨은 축 왼쪽에 그려져야 한다(2026-09-14). x축 테스트와
    같은 이유로 tick_step_moa=10.0을 사용해 길이를 기본값으로 고정한다."""
    frame = np.zeros((600, 600, 3), dtype=np.uint8)
    profile = _profile()
    ox_i = int(round(_OX))
    oy_i = int(round(_OY))
    length = 4

    out = draw_coordinate_axes(frame, profile, tick_step_moa=10.0, label_step_moa=10.0, max_moa_range=10)

    font_scale = max(0.4, min(1.0, 600 / 2400))
    (text_w, _), _ = cv2.getTextSize("10", cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)

    # +y 라벨: 구현 공식상 x=ox_i+length+3부터 오른쪽으로 text_w만큼
    right_col_range = slice(ox_i + length + 2, ox_i + length + 3 + text_w + 2)
    # -y 라벨: x=ox_i-length-3-text_w부터 오른쪽(=ox_i 쪽)으로 text_w만큼
    left_col_range = slice(max(0, ox_i - length - 3 - text_w - 2), ox_i - length - 2)

    assert out[:, right_col_range, 2].any()  # +y 라벨(축 오른쪽)이 그려져 있어야 함
    assert out[:, left_col_range, 2].any()  # -y 라벨(축 왼쪽)이 그려져 있어야 함

    # 두 열 범위 모두 원점 근처라 가로 축선 자체(oy_i, 두께만큼)가 지나가므로, 그 행은
    # 라벨과 무관하게 항상 걸린다 - 라벨 행만 비교하려면 축선이 걸리는 행을 제외해야 한다.
    axis_band = slice(max(0, oy_i - 3), min(600, oy_i + 4))
    right_mask = out[:, right_col_range, 2].any(axis=1)
    left_mask = out[:, left_col_range, 2].any(axis=1)
    right_mask[axis_band] = False
    left_mask[axis_band] = False

    # +y는 화면 y가 원점보다 위쪽(작은 행), -y는 아래쪽(큰 행)이므로 서로 다른 행 범위임을 확인
    right_rows = np.where(right_mask)[0]
    left_rows = np.where(left_mask)[0]
    assert right_rows.max() < left_rows.min()
