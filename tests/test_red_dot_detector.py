import math

import numpy as np
import cv2

from core.vision.red_dot_detector import RedDotDetector, DetectionResult
from core.vision.blob_tracker import BlobTracker
from core.config.settings import DetectionSettings


def _make_frame(width=400, height=300, dot_center=(200, 150), dot_size=(10, 10), color_bgr=(40, 160, 230)):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 1] = 60  # 초록 배경
    cv2.ellipse(frame, dot_center, dot_size, 0, 0, 360, color_bgr, -1)
    return frame


def test_detects_circular_dot_near_center():
    frame = _make_frame(dot_center=(200, 150), dot_size=(8, 8))
    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)
    assert result.found
    cx, cy = result.center_px
    assert abs(cx - 200) < 2
    assert abs(cy - 150) < 2


def test_excludes_oversized_blob_from_candidates():
    """실측 재생 영상에서 배경(초록 발광 영역) 전체가 HSV 임계값에 걸려 프레임의 2/3에
    달하는 거대한 가짜 블롭이 검출되고, area 내림차순 정렬 때문에 진짜(작은) 레드닷보다
    우선 선택되어 엉뚱한 고정 위치가 계속 선택되는 문제가 있었다(실측으로 확인,
    2026-09-14: 1등 후보 면적이 4,323,714px2). max_blob_area로 이런 비정상적으로 큰
    블롭은 후보에서 아예 제외해야 한다."""
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    frame[..., 1] = 60
    # 프레임 상당 부분을 덮는 거대한 가짜 블롭(배경 오염 시뮬레이션) - 아래쪽에는 검은
    # 여백을 남겨 진짜 레드닷(작은 원)과 서로 다른 윤곽선으로 분리되게 한다.
    cv2.rectangle(frame, (5, 5), (395, 190), (40, 160, 230), -1)
    # 실제 레드닷 크기의 작은 원 하나(위 블롭과 겹치지 않는 아래쪽)
    cv2.circle(frame, (100, 250), 8, (40, 160, 230), -1)

    detector = RedDotDetector(DetectionSettings())
    candidates = detector.detect(frame)

    assert all(c.area_px2 <= DetectionSettings().max_blob_area for c in candidates)
    assert len(candidates) == 1  # 거대 블롭은 제외되고 작은 원만 후보로 남음
    cx, cy = candidates[0].center_px
    assert abs(cx - 100) < 2
    assert abs(cy - 250) < 2


def test_excludes_low_circularity_candidate_even_with_similar_area():
    """면적만으로는 진짜 레드닷과 구분 안 되는 가짜 후보(그리드 문자/눈금 반사처럼
    삐죽삐죽/길쭉한 모양)가 실측으로 확인됐다(2026-09-14: 진짜 522px2, 가짜 553px2로
    거의 같은 면적이라 면적순 정렬만으로는 가짜가 이겼음). 원형도(circularity) 필터로
    이런 비원형 후보를 걸러내야 한다."""
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    frame[..., 1] = 60
    # 진짜 레드닷: 둥근 원
    cv2.circle(frame, (100, 150), 13, (40, 160, 230), -1)
    # 가짜 후보: 가늘고 긴 십자(+) 모양 - 원과 면적대는 비슷할 수 있어도 원형도가 훨씬 낮음
    cv2.rectangle(frame, (225, 145), (375, 155), (40, 160, 230), -1)  # 가로 막대
    cv2.rectangle(frame, (295, 75), (305, 225), (40, 160, 230), -1)  # 세로 막대

    detector = RedDotDetector(DetectionSettings())
    candidates = detector.detect(frame)

    centers = [c.center_px for c in candidates]
    assert any(abs(cx - 100) < 3 and abs(cy - 150) < 3 for cx, cy in centers)  # 진짜 원은 남아있어야 함
    assert not any(200 < cx < 400 for cx, cy in centers)  # 십자 모양은 전부 제외돼야 함


def test_detects_elliptical_dot_at_edge():
    frame = _make_frame(dot_center=(350, 50), dot_size=(12, 6))  # 타원형 왜곡 시뮬레이션
    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)
    assert result.found
    assert result.ellipse is not None
    cx, cy = result.center_px
    assert abs(cx - 350) < 2
    assert abs(cy - 50) < 2


def _hsv_to_bgr(h: int, s: int, v: int) -> tuple[int, int, int]:
    bgr = cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0][0]
    return tuple(int(c) for c in bgr)


def _make_comet_tail_frame(
    width=400, height=300, head_center=(200, 150), head_radius=8, tail_dx=1, tail_len=30, n_segments=8
):
    """35MOA 근처에서 실측된 "코멧테일"(밝은 머리 + 중심 반대쪽으로 흐려지는 꼬리) 형태를
    흉내낸 합성 이미지 - 머리는 밝고(V=230) 꼬리는 점점 어두워짐(V가 130까지 내려감,
    HSV 임계값 하한(120)보다는 위라 여전히 같은 마스크에 포함됨)."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 1] = 60
    cv2.circle(frame, head_center, head_radius, _hsv_to_bgr(20, 200, 230), -1)
    hx, hy = head_center
    for i in range(1, n_segments + 1):
        frac = i / n_segments
        v = max(int(230 - frac * 100), 120)
        px = int(hx + tail_dx * frac * tail_len)
        r = max(1, int(6 * (1 - frac * 0.7)))
        cv2.circle(frame, (px, hy), r, _hsv_to_bgr(20, 200, v), -1)
    return frame


def test_head_square_center_is_pulled_toward_bright_head_not_binary_centroid():
    """35MOA 부근에서 실측된 "코멧테일" 왜곡(고객 제공 영상으로 확인) - 이진 마스크의 단순
    무게중심(모든 픽셀 동일 가중치)은 어두운 꼬리 쪽으로 쏠린다. 정사각형 분할 방식
    (_fit_head_square, 14차 수정 이후 center_px의 주된 계산 방식)이 그 쏠림을 뚜렷이 줄여
    참 중심(밝은 머리)에 더 가까워야 한다."""
    true_center_x = 200
    frame = _make_comet_tail_frame(head_center=(true_center_x, 150), tail_dx=1)

    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)
    assert result.found

    # 참고용 - 단순 이진 마스크 무게중심(꼬리 포함, 쏠림 발생) 직접 계산
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    s = DetectionSettings()
    mask1 = cv2.inRange(hsv, np.array(s.hsv_lower1), np.array(s.hsv_upper1))
    mask2 = cv2.inRange(hsv, np.array(s.hsv_lower2), np.array(s.hsv_upper2))
    mask = cv2.bitwise_or(mask1, mask2)
    contour = sorted(
        cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0],
        key=cv2.contourArea,
        reverse=True,
    )[0]
    moments = cv2.moments(contour)
    naive_center_x = moments["m10"] / moments["m00"]

    naive_bias = abs(naive_center_x - true_center_x)
    detector_bias = abs(result.center_px[0] - true_center_x)
    assert naive_bias > 3.0  # 꼬리 쪽으로 유의미하게 쏠림(회귀 확인용)
    assert detector_bias < naive_bias * 0.5  # 정사각형 분할 방식으로 쏠림이 뚜렷이 줄어듦


def test_core_circle_is_round_even_when_ellipse_is_stretched_by_comet_tail():
    """core_circle은 꼬리를 뺀 밝은 "머리"만의 형상이므로, 꼬리가 길어질수록 늘어나는
    ellipse와 달리 꼬리 길이와 거의 무관하게 일정한 반지름을 유지해야 한다(실제 LED 광원
    자체는 항상 원형이라는 물리적 사실과 일치 - docs/detection_notes.md 10차 참고).

    두 tail_len 모두 _fit_head_square의 최소 길쭉함 기준(세로/가로 2:1, 2026-09-14 추가 -
    살짝만 길쭉한 거의-원형 블롭에 이 로직을 적용하면 밝기가 비슷한 두 조각 중 노이즈로
    하나를 잘못 고르는 문제가 실측으로 확인됨)을 넘도록 길이를 잡는다 - 그래야 "짧은 꼬리
    vs 긴 꼬리 모두 core_circle이 안정적"이라는 이 테스트의 취지가 유지된다."""
    detector = RedDotDetector(DetectionSettings())

    short_tail = _make_comet_tail_frame(head_center=(200, 150), tail_len=25)
    long_tail = _make_comet_tail_frame(head_center=(200, 150), tail_len=45)

    short_result = detector.detect_best(short_tail)
    long_result = detector.detect_best(long_tail)
    assert short_result.found and long_result.found
    assert short_result.core_circle is not None and long_result.core_circle is not None

    # ellipse(꼬리 포함)는 꼬리가 길어지면 뚜렷이 늘어난다(회귀 확인용).
    short_major = max(short_result.ellipse[1])
    long_major = max(long_result.ellipse[1])
    assert long_major > short_major * 1.3

    # core_circle 반지름은 꼬리 길이와 거의 무관하게 유지되어야 한다.
    short_radius = short_result.core_circle[1]
    long_radius = long_result.core_circle[1]
    assert abs(long_radius - short_radius) < short_radius * 0.3


def test_mildly_elongated_blob_skips_head_square_and_uses_centroid():
    """세로/가로 비율이 2:1 미만인(거의 원형) 블롭은 _fit_head_square 대신 무게중심
    계산으로 폴백해야 한다 - 실측 재생 영상에서 세로/가로 1.5:1인 블롭을 이 로직이
    2조각으로 나눠 비교하다가, 거의 원형이라 밝기가 엇비슷한 두 조각 중 노이즈로 어두운
    꼬리 쪽을 "머리"로 잘못 골라 무게중심(y=447.3)보다 10px 이상 어긋난 y=457.0을
    내놓은 회귀에 대한 테스트(2026-09-14)."""
    # tail_dx=0.3으로 약하게만 늘려 세로/가로 비율이 2:1 미만이 되게 한다.
    frame = _make_comet_tail_frame(head_center=(200, 150), tail_dx=0.3, tail_len=8, n_segments=8)

    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)

    assert result.found
    assert result.core_circle is None  # 길쭉함 기준 미달로 head_square 적용 안 됨


def test_elongated_but_uniform_brightness_blob_also_falls_back_to_centroid():
    """세로/가로 비율이 2:1을 넘어도(길쭉함 기준은 통과), 조각 간 밝기 차이가 뚜렷하지
    않으면(노이즈 수준) 여전히 head_square 결과를 신뢰하면 안 된다 - "크기 기준만 바꾸면
    우연히 또 엉뚱한 조각을 고르는 경우가 재발하지 않겠냐"는 지적(2026-09-14)에 대한
    회귀 테스트. 길쭉한 타원이지만 전체가 균일한 밝기라 진짜 코멧테일(뚜렷한 밝기 차이)이
    아닌 경우를 흉내낸다."""
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    frame[..., 1] = 60
    # 세로 36 / 가로 16 = 2.25:1로 길쭉함 기준(2.0)은 넘는 블롭을 위아래 두 조각으로
    # 붙여 만들되, 밝기 차이를 5%(V=205 vs 195)로 아주 작게 둔다 - 2% 동률 허용치는
    # 넘어서 한쪽이 "승자"가 되지만, 새로 추가한 15% 신뢰 기준에는 못 미치는 애매한 차이.
    cv2.rectangle(frame, (192, 132), (208, 150), _hsv_to_bgr(20, 200, 205), -1)  # 위쪽(살짝 더 밝음)
    cv2.rectangle(frame, (192, 150), (208, 168), _hsv_to_bgr(20, 200, 195), -1)  # 아래쪽

    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)

    assert result.found
    assert result.core_circle is None  # 밝기 차이가 애매해 확신 부족 -> 무게중심 폴백


def test_no_dot_returns_not_found():
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    frame[..., 1] = 60
    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)
    assert not result.found


def test_blob_tracker_picks_nearest_to_previous():
    detector = RedDotDetector(DetectionSettings())
    tracker = BlobTracker(max_jump_px=30)

    # 1프레임: 하나의 후보
    frame1 = _make_frame(dot_center=(100, 100))
    r1 = tracker.select(detector.detect(frame1))
    assert r1.found
    assert abs(r1.center_px[0] - 100) < 2

    # 2프레임: 실제 대상은 조금 이동, 노이즈성 후보(멀리 있는 것)도 추가
    frame2 = _make_frame(dot_center=(110, 105))
    cv2.circle(frame2, (350, 250), 6, (40, 160, 230), -1)  # 멀리 있는 노이즈 블롭
    r2 = tracker.select(detector.detect(frame2))
    assert r2.found
    assert abs(r2.center_px[0] - 110) < 4
    assert abs(r2.center_px[1] - 105) < 4


def _detection(cx: float, cy: float, major: float, minor: float):
    return DetectionResult(
        found=True,
        center_px=(cx, cy),
        ellipse=((cx, cy), (minor, major), 0.0),
        area_px2=major * minor,
    )


def test_blob_tracker_blends_toward_prediction_only_when_elongated():
    """정상적인 원형 구간(elongation < threshold)의 측정치는 그대로 신뢰하고, 심한
    코멧테일 왜곡 구간(elongation >= threshold)에서만 이전 속도 기반 예측치와 blend해
    단일 프레임 흔들림을 억제해야 한다(사용자 확인 사항, 2026-09-13)."""
    tracker = BlobTracker(max_jump_px=60, elongation_correction_threshold=1.5, elongation_correction_blend=0.5)

    tracker.select([_detection(100, 100, 8, 8)])  # 1프레임: 시드
    tracker.select([_detection(110, 100, 8, 8)])  # 2프레임: 속도(+10, 0) 확립

    # 3프레임: 측정치가 25px 튀었지만(노이즈) 심하게 늘어진 형상(elongation=3.75)
    r3 = tracker.select([_detection(135, 100, 30, 8)])
    predicted_x = 120.0  # 110 + (110-100)
    assert abs(r3.center_px[0] - 127.5) < 0.5  # 측정치(135)와 예측치(120)의 중간으로 보정됨
    assert r3.center_px[0] != 135.0


def test_blob_tracker_correction_does_not_compound_across_consecutive_elongated_frames():
    """여러 프레임 연속으로 elongation이 임계값을 넘으면(코멧테일 구간이 몇 프레임 이어지는
    실측 사례), 예측이 "이전 보정값"이 아니라 항상 "이전 원시 측정치"를 근거로 계산되어야
    한다 - 그렇지 않으면 매 프레임 오차가 누적되어 결과가 실제 위치에서 점점 멀어진다
    (실측으로 확인된 회귀 버그, docs/detection_notes.md 12차 후속 수정)."""
    tracker = BlobTracker(max_jump_px=200, elongation_correction_threshold=1.5, elongation_correction_blend=0.5)

    tracker.select([_detection(1629, 444, 8, 8)])  # 정상 원형 구간
    # 아래는 실측(frame 19~22)을 단순화한 패턴: 측정치가 예측(등속 외삽) 대비 계속 "덜 이동"함
    # (감속하며 목표에 다가가는 흔한 패턴) - 매 프레임 원시 측정치를 근거로 예측해야 함.
    r1 = tracker.select([_detection(1624, 408, 30, 8)])   # elongation=3.75
    r2 = tracker.select([_detection(1605, 370, 30, 8)])
    r3 = tracker.select([_detection(1602, 351, 30, 8)])
    r4 = tracker.select([_detection(1603, 347, 30, 8)])  # 실제로는 거의 안 움직임(방향 반전 근처)

    # 마지막 결과가 원시 측정치(1603, 347)에서 크게 벗어나면 안 된다 - 누적 드리프트
    # 회귀 확인용(수정 전에는 여러 프레임 연속 보정으로 원시치에서 10px 이상 벗어났었음).
    assert math.hypot(r4.center_px[0] - 1603, r4.center_px[1] - 347) < 8.5


def test_blob_tracker_does_not_blend_circular_dot():
    """elongation이 임계값 미만(정상 원형)이면 측정치를 그대로 반환해야 한다."""
    tracker = BlobTracker(max_jump_px=60, elongation_correction_threshold=1.5, elongation_correction_blend=0.5)

    tracker.select([_detection(100, 100, 8, 8)])
    tracker.select([_detection(110, 100, 8, 8)])

    r3 = tracker.select([_detection(135, 100, 8, 8)])  # 같은 튐이지만 원형
    assert abs(r3.center_px[0] - 135.0) < 1e-9


def test_blob_tracker_rejects_jump_beyond_max():
    detector = RedDotDetector(DetectionSettings())
    tracker = BlobTracker(max_jump_px=10)

    frame1 = _make_frame(dot_center=(100, 100))
    tracker.select(detector.detect(frame1))

    frame2 = _make_frame(dot_center=(300, 250))  # 너무 멀리 뜀 (실제로는 불가능한 이동)
    r2 = tracker.select(detector.detect(frame2))
    assert not r2.found
