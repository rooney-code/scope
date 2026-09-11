# 레드닷 검출 참고 노트

## 참고 이미지 관찰 (밝기 8단계 / 7단계, 중심 부근)

- 실제 색상은 순수 빨강(hue 0)이 아니라 **주황~호박색(amber, hue 약 15~35)** 에 가깝고, 중심이 밝고 가장자리로 갈수록 부드럽게 퍼지는 발광(bloom)이 있음.
- 배경은 어두운 초록색(보어사이터 조명 반사) — 초록 배경과 주황 레드닷의 색상 대비는 크므로 HSV hue 기반 분리가 유효할 것으로 판단.
- 중심 부근에서는 형태가 원형에 가까움(지름 대략 그리드 반지름의 1/30~1/40 수준, 실측 필요). 계획서에 기록된 대로 35MOA 부근 등 이동 범위 끝에서는 타원형으로 왜곡되는 경우가 있음 — `fit_ellipse` 기반 처리 필요.
- 십자선(격자)은 어두운 색으로 미세하게 보이며 tick 라벨 텍스트도 함께 인쇄되어 있어, Hough Line 검출 시 텍스트를 라인으로 오검출하지 않도록 최소 길이(minLineLength)를 충분히 크게 잡아야 함.

## HSV 기본값 조정 (v1 시작값, 실기 튜닝 전 가정)

기존 계획의 "레드"보다 호박색(amber) 쪽으로 hue 범위를 이동:

```
hsv_lower1 = (5, 80, 120)     # amber 하한 (H,S,V) - OpenCV H range 0-179
hsv_upper1 = (35, 255, 255)   # amber 상한
hsv_lower2 = (0, 0, 0)        # wrap-around(순수 빨강 근접치) 대비용 - 필요시만 사용
hsv_upper2 = (4, 255, 255)
min_blob_area = 15            # px^2, 실기에서 재조정
```

실기 캡처 영상으로 1차 튜닝 후 `core/config/settings.py`의 detection 섹션에 반영할 것.

## 실제 현장 이미지 검증 결과 (2026-09-11)

현장에서 촬영한 실제 이미지 3장(`tests/test_images/originals/`: 조리개최대.jpg, 8단계.jpg,
좌하단_7단계.jpg, 카메라 실해상도 3088x2076 = IDS U3-3880LE-C 스펙과 일치)으로 검증.

- **레드닷 검출(HSV)**: 위 기본값 그대로 8단계/7단계 이미지 모두에서 단일 블롭으로 깔끔하게
  검출됨(다른 노이즈 후보 없음). 별도 튜닝 불필요했음.
- **그리드 자동검출(GridAutoDetector)**: 초기 기본값(Canny 30/100, Hough threshold=80,
  minLineLength=프레임의 30%)은 실제 사진에서 **전혀 검출되지 않았음** - 실제 십자선은 대비가
  낮고 tick/라벨 텍스트에 가려 짧은 조각들로 끊겨 보이기 때문. 아래 값으로 재튜닝 후 정상
  검출됨(원점 약 (1605, 681)px, 이미지 중앙(1544, 1038)과는 거리가 있음 - 설치 상태에 따라
  원점이 중앙이 아닐 수 있다는 점과 일치):
  ```
  canny_threshold1=10, canny_threshold2=50
  hough_threshold=40, min_line_length_ratio=0.1, max_line_gap=40
  ```
  또한 가장 긴 선 하나만 쓰지 않고 인접한(±5px) 조각들을 평균 내는 클러스터링을 추가해
  안정성을 높임 (`GridAutoDetector._cluster_axis_position`). 이 값들이 새 기본값으로
  `core/calibration/grid_auto_detector.py`에 반영됨.
- **tick 자동 간격 추정**은 여전히 노이즈가 있어(라벨 텍스트와 혼동) 완전히 신뢰하기 어려움 -
  계획대로 클릭 스냅/화살표 미세조정을 통한 수동 확정이 필요.
- 재현 방법: `PYTHONPATH=. python3 scripts/detect_on_real_images.py` (결과는
  `tests/test_images/results/`에 저장됨).
