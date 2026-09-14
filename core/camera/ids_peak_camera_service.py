"""IDS U3-3880LE-C-HQ (REV.1.2)용 실제 카메라 서비스.

IDS peak SDK(ids_peak, ids_peak_ipl)의 Python 바인딩을 사용한다. 이 SDK는 IDS Software Suite와
함께 별도로 설치해야 하며 PyPI에서 항상 받을 수 있는 것이 아니므로, 이 모듈은 지연 임포트(lazy
import)로 작성한다 - SDK가 없는 개발 환경에서도 나머지 코드는 정상적으로 임포트/테스트 가능해야 함.

노드명(GenApi 이름)은 계획 문서(logical-herding-teapot.md)의 카메라 기본값 표 및 첨부된 GenApi
persistence 파일을 참고한 추정치이며, 실기에서 `ids_peak`의 NodeMap을 통해 1회 검증이 필요하다
(TBD로 표시된 항목들).
"""
from __future__ import annotations

import threading
import time
from dataclasses import fields, replace
from typing import Any

import numpy as np

from core.camera.camera_service import CameraInfo, FrameCallback, ICameraService
from core.config.settings import CameraSettings


class IdsPeakNotAvailableError(RuntimeError):
    """ids_peak SDK를 임포트할 수 없을 때 발생 (미설치 환경)."""


# CameraSettings 필드 이름 -> 실제 카메라 GenApi 노드 이름. apply_settings()/read_settings()가
# 공유한다. 여기 없는 필드(auto_function_owner, digital_gain_r/g/b, wb_gain_r/g/b,
# brightness_*, color_correction_*, saturation*, chromatic_adaption_*)는 계획 문서 단계의
# 자리만 마련된 것으로, 카메라에 실제로 연동된 적이 없다(wb_gain_r/g/b는 애초에 이 카메라
# NodeMap에 BalanceRatio 계열 노드가 없음을 실기로 확인함 - scripts/list_camera_nodes.py,
# 2026-09-14) - read_settings()가 이런 필드들을 전부 "편집해도 소용없음"으로 표시한다.
_CAMERA_BACKED_FIELDS: dict[str, str] = {
    "frame_rate_fps": "AcquisitionFrameRate",
    "exposure_time_us": "ExposureTime",
    "device_link_throughput_limit_bps": "DeviceLinkThroughputLimit",
    "analog_gain": "Gain",
    "black_level": "BlackLevel",
    "black_level_auto": "BlackLevelAuto",
    "auto_exposure": "ExposureAuto",
    "auto_gain": "GainAuto",
    "auto_white_balance": "BalanceWhiteAuto",
}


class IdsPeakCameraService(ICameraService):
    def __init__(self, device_serial: str = "") -> None:
        self._device_serial = device_serial
        self._device = None
        self._datastream = None
        self._nodemap = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._ipl = None
        self._peak = None
        # 화이트밸런스 R/G/B 게인(host-side) - 이 카메라(U3-388xLE-C)는 BalanceRatio 노드가
        # 없어(실기 확인 사항) apply_settings()가 ids_peak_ipl.Gain으로 만들어 채운다.
        # _buffer_to_bgr()에서 컬러 변환 전(raw Bayer/Mono 단계)에 적용된다.
        self._wb_gain = None

    # ---- ICameraService ----
    def open(self) -> CameraInfo:
        peak, ipl = self._import_sdk()
        peak.Library.Initialize()
        device_manager = peak.DeviceManager.Instance()
        device_manager.Update()
        devices = device_manager.Devices()
        if not devices:
            raise RuntimeError("연결된 IDS 카메라를 찾을 수 없습니다.")

        target = devices[0]
        if self._device_serial:
            matches = [d for d in devices if d.SerialNumber() == self._device_serial]
            if not matches:
                raise RuntimeError(f"지정한 시리얼({self._device_serial})의 카메라를 찾을 수 없습니다.")
            target = matches[0]

        self._device = target.OpenDevice(peak.DeviceAccessType_Control)
        self._nodemap = self._device.RemoteDevice().NodeMaps()[0]
        self._datastream = self._device.DataStreams()[0].OpenDataStream()
        return CameraInfo(
            device_id=self._device.SerialNumber(),
            model_name=self._device.ModelName() if hasattr(self._device, "ModelName") else "IDS U3-3880LE-C-HQ",
            serial_number=self._device.SerialNumber(),
        )

    def start(self, on_frame: FrameCallback) -> None:
        if self._running or self._nodemap is None:
            return
        peak, ipl = self._peak, self._ipl

        payload_size = self._nodemap.FindNode("PayloadSize").Value()
        num_buffers = self._datastream.NumBuffersAnnouncedMinRequired()
        for _ in range(num_buffers):
            buf = self._datastream.AllocAndAnnounceBuffer(payload_size)
            self._datastream.QueueBuffer(buf)

        self._datastream.StartAcquisition()
        self._nodemap.FindNode("TLParamsLocked").SetValue(1)
        self._nodemap.FindNode("AcquisitionStart").Execute()

        self._running = True

        def _loop() -> None:
            while self._running:
                try:
                    buffer = self._datastream.WaitForFinishedBuffer(1000)
                except Exception:
                    continue
                frame = self._buffer_to_bgr(buffer, ipl)
                self._datastream.QueueBuffer(buffer)
                if frame is not None:
                    on_frame(frame)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._nodemap is not None:
            try:
                self._nodemap.FindNode("AcquisitionStop").Execute()
                self._nodemap.FindNode("TLParamsLocked").SetValue(0)
            except Exception:
                pass
        if self._datastream is not None:
            try:
                self._datastream.StopAcquisition()
            except Exception:
                pass

    def close(self) -> None:
        self.stop()
        self._device = None
        self._datastream = None
        self._nodemap = None

    def apply_settings(self, settings: CameraSettings) -> None:
        """계획 문서의 카메라 기본값 표를 노드맵에 적용.

        순서 중요: 자동기능(Auto Exposure/Gain/WhiteBalance/BlackLevelAuto)을 먼저 Off로
        설정한 뒤 수동값을 써야 한다 - SDK가 Auto On 상태에서 수동값 설정을 무시/거부할 수 있음.
        정확한 노드명은 실기에서 NodeMap 탐색으로 검증 필요(TBD로 표시된 항목들). 단,
        wb_gain_r/g/b는 실기 확인 결과(scripts/list_camera_nodes.py) 이 카메라에 해당
        노드 자체가 없어 호스트 측 구현이 필요함을 확인했다 - 아래 해당 줄 주석 참고.

        카메라가 이미 스트리밍 중(start() 호출 이후, 카메라 설정 화면의 "적용" 버튼처럼)이면
        TLParamsLocked=1 상태라 대부분의 노드가 잠겨 있어 그대로 쓰면 전부
        "Node is not writable" 에러가 난다(실기 로그로 확인, 2026-09-14) - 적용하는 동안만
        잠깐 정지(AcquisitionStop + TLParamsLocked=0)했다가 끝나면 원래대로 되돌린다.
        """
        nm = self._nodemap
        if nm is None:
            raise RuntimeError("카메라가 열려 있지 않습니다. open()을 먼저 호출하세요.")

        peak = self._peak
        not_settable_statuses = {peak.NodeAccessStatus_NotImplemented, peak.NodeAccessStatus_NotAvailable}

        def _try_set(node_name: str, value: Any) -> None:
            try:
                node = nm.FindNode(node_name)
                # 접근상태가 NotImplemented/NotAvailable이면 이 기능 자체가 카메라
                # 하드웨어/펌웨어에 없는 것이다 - 일시적인 게 아니라 영구적인 상태임을
                # 실기로 확인함(카메라를 단독으로 잡은 상태에서도 동일, 2026-09-14).
                # ExposureAuto/GainAuto/BalanceWhiteAuto가 대표적인 예: 이 카메라는
                # 자동 노출/게인/화이트밸런스를 카메라 자체적으로 지원하지 않고, IDS peak
                # Cockpit이 보여주는 "자동" 토글은 카메라 노드가 아니라 호스트(PC) 소프트웨어
                # 자체 제어 루프다(화이트밸런스 R/G/B 게인에 대응하는 BalanceRatio 노드가
                # 이 카메라에 아예 없는 것과 같은 맥락 - scripts/list_camera_nodes.py로 확인).
                # 우리 프로그램은 이런 호스트 측 자동 제어를 구현하지 않았으므로(설정 기본값도
                # 전부 "off", 수동 고정값 운용이 이 프로그램의 정상 동작), SetCurrentEntry/
                # SetValue를 시도해도 의미가 없어 "실패"로 로그를 남기지 않고 조용히 건너뛴다.
                if node.AccessStatus() in not_settable_statuses:
                    return
                # Enum형 노드(ExposureAuto="Off" 같은 문자열 선택지)는 SetValue가 아니라
                # SetCurrentEntry로 설정해야 한다 - SetValue를 부르면
                # "'EnumerationNode' object has no attribute 'SetValue'"로 실패한다
                # (실기 로그로 확인된 버그, 2026-09-14). Integer/Float/String/Boolean
                # 노드는 SetCurrentEntry가 없으므로 기존대로 SetValue를 쓴다.
                if hasattr(node, "SetCurrentEntry"):
                    node.SetCurrentEntry(value)
                    return

                # Integer/Float 노드는 허용 범위(Minimum~Maximum)가 다른 설정값(예: 노출
                # 시간)에 따라 동적으로 바뀐다 - 예를 들어 AcquisitionFrameRate의 최대값은
                # ExposureTime이 길수록 낮아진다. settings.json에 저장된 값이 지금 카메라
                # 상태의 허용 범위를 벗어나면 OutOfRangeException으로 그냥 실패했었는데
                # (실기 로그로 확인, 2026-09-14), 이제는 범위 안으로 자동으로 맞춰서 적용한다.
                clamped = value
                if hasattr(node, "Minimum") and hasattr(node, "Maximum"):
                    lo, hi = node.Minimum(), node.Maximum()
                    clamped = max(lo, min(hi, value))
                    if clamped != value:
                        print(f"[camera] {node_name} = {value}가 허용 범위({lo}~{hi})를 벗어나 {clamped}로 조정")

                node.SetValue(clamped)
            except Exception as exc:  # noqa: BLE001 - 현장 노드명 검증 전까지는 로그만
                print(f"[camera] 설정 실패(노드명 확인 필요): {node_name} = {value} ({exc})")

        was_running = self._running
        if was_running:
            try:
                self._nodemap.FindNode("AcquisitionStop").Execute()
                self._nodemap.FindNode("TLParamsLocked").SetValue(0)
                # AcquisitionStop()이 반환돼도 스트림이 실제로 멎기까지 짧은 지연이 있을 수
                # 있어(BlackLevelAuto/DeviceLinkThroughputLimit이 정지 직후보다 조금 뒤에
                # 쓰면 성공하는 패턴이 실기 로그로 확인됐다, 2026-09-14) 짧게 대기한다.
                # 단, ExposureAuto/GainAuto/BalanceWhiteAuto가 계속 실패하는 건 이 지연과
                # 무관하다 - 이 셋은 이 카메라에 해당 기능 자체가 없어(NotImplemented, 카메라
                # 단독 접근 상태에서도 동일하게 확인됨) 아무리 기다려도 성공하지 않는다.
                time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001 - 잠금 해제 실패해도 아래 _try_set들이 개별로 실패/로그됨
                print(f"[camera] 설정 적용을 위한 일시 정지 실패: {exc}")

        # 1) 자동기능 끄기 - ExposureAuto/GainAuto/BalanceWhiteAuto는 이 카메라에 없는
        # 기능이라(위 _try_set의 NotImplemented 처리 참고) 항상 조용히 건너뛰어지고,
        # BlackLevelAuto만 실제로 적용된다.
        _try_set("ExposureAuto", "Off")
        _try_set("GainAuto", "Off")
        _try_set("BalanceWhiteAuto", "Off")
        _try_set("BlackLevelAuto", "Off")

        # 2) 수동값 적용 - ExposureTime을 AcquisitionFrameRate보다 먼저 적용한다: 최대
        # 프레임률은 노출 시간이 길수록 낮아지는 식으로 서로 연동되어 있어(실기 확인,
        # 2026-09-14), 순서가 반대면 프레임률 범위 클램프가 "아직 안 바뀐" 노출 시간
        # 기준으로 계산되어 새 노출 시간에서는 더 높은 프레임률도 가능한데 불필요하게
        # 낮게 잡힐 수 있다.
        _try_set("ExposureTime", settings.exposure_time_us)
        _try_set("AcquisitionFrameRate", settings.frame_rate_fps)
        _try_set("DeviceLinkThroughputLimit", settings.device_link_throughput_limit_bps)
        _try_set("Gain", settings.analog_gain)  # GainSelector=AnalogAll 전제, 실기 확인 필요
        _try_set("BlackLevel", settings.black_level)
        # wb_gain_r/g/b(BalanceRatioSelector/BalanceRatio)는 이 카메라의 NodeMap에 없다 -
        # scripts/list_camera_nodes.py Balance로 실기에서 직접 확인한 결과(2026-09-14),
        # BalanceWhiteAuto 관련 노드만 있고 Ratio 계열 노드가 전혀 없다. 이 카메라는
        # R/G/B 화이트밸런스 게인을 카메라가 아니라 호스트(PC, ids_peak_ipl) 쪽에서
        # 처리하는 구조로 보인다(Cockpit "컬러" 패널의 "자동 기능: 호스트" 선택과 일치) -
        # 카메라 노드로 시도하면 항상 NOT_FOUND라 아예 시도하지 않는다. 호스트 측에서
        # 실제로 게인을 적용하려면 _buffer_to_bgr()의 프레임 변환 단계에 곱해줘야 하는데,
        # 아직 구현 안 됨(TBD) - 지금은 wb_gain_r/g/b 설정값이 화면에 반영되지 않는다.

        if was_running:
            try:
                self._nodemap.FindNode("TLParamsLocked").SetValue(1)
                self._nodemap.FindNode("AcquisitionStart").Execute()
            except Exception as exc:  # noqa: BLE001
                print(f"[camera] 설정 적용 후 재시작 실패: {exc}")

    def read_settings(self, base: CameraSettings) -> tuple[CameraSettings, set[str]]:
        """_CAMERA_BACKED_FIELDS에 있는 필드만 카메라의 현재 값으로 덮어쓴다 - 나머지
        필드(계획 자리만 마련된 것들)는 base 값을 그대로 둔다. 두 번째 반환값은 "편집해도
        소용없는" 필드 이름 집합: 연동 자체가 안 된 필드 + 연동은 됐지만 지금 상태에서
        NotImplemented/NotAvailable/ReadOnly인 필드."""
        nm = self._nodemap
        if nm is None:
            raise RuntimeError("카메라가 열려 있지 않습니다. open()을 먼저 호출하세요.")
        peak = self._peak

        result = replace(base)
        read_only = {f.name for f in fields(CameraSettings) if f.name not in _CAMERA_BACKED_FIELDS}

        for field_name, node_name in _CAMERA_BACKED_FIELDS.items():
            try:
                node = nm.FindNode(node_name)
                status = node.AccessStatus()
                if status in (peak.NodeAccessStatus_NotImplemented, peak.NodeAccessStatus_NotAvailable):
                    read_only.add(field_name)
                    continue
                if status == peak.NodeAccessStatus_ReadOnly:
                    read_only.add(field_name)

                raw_value = node.CurrentEntry().SymbolicValue() if hasattr(node, "CurrentEntry") else node.Value()
                current_default = getattr(result, field_name)
                # 필드의 기존 타입(bool/int/float/str)에 맞춰 캐스팅 - CameraSettings는
                # 필드마다 타입이 고정돼 있어 카메라가 돌려준 값을 그대로 넣으면(예: 카메라는
                # float인데 필드는 int) 타입이 안 맞을 수 있다. Enum형 노드의 SymbolicValue는
                # "Off"처럼 대문자로 시작하는데, CameraSettings는 "off"처럼 전부 소문자
                # 관례라(_ON_OFF_FIELDS 콤보박스 항목도 소문자) 문자열이면 소문자로 맞춘다 -
                # 안 그러면 콤보박스가 "Off"를 목록에서 못 찾아 표시가 안 바뀐다.
                if isinstance(current_default, str):
                    raw_value = str(raw_value).lower()
                setattr(result, field_name, type(current_default)(raw_value))
            except Exception as exc:  # noqa: BLE001
                print(f"[camera] 설정 읽기 실패: {node_name} ({exc})")
                read_only.add(field_name)

        return result, read_only

        # 3) 화이트밸런스 R/G/B 게인 - 호스트 측(소프트웨어) 적용
        # 위 BalanceRatio 노드 시도는 이 카메라(U3-388xLE-C) 모델에는 그 노드 자체가 없어
        # 항상 실패 로그만 남긴다(실기 확인 사항 - docs/windows_setup_guide.md 참고). 카메라가
        # 이 기능을 지원하지 않으므로, ids_peak_ipl.Gain으로 캡처된 각 프레임에 직접 R/G/B
        # 게인을 곱하는 방식으로 대신 구현한다(_buffer_to_bgr()에서 실제 적용).
        self._update_white_balance_gain(settings)

    def _update_white_balance_gain(self, settings: CameraSettings) -> None:
        """ids_peak_ipl.Gain은 채널별 유효 범위가 있고(실측: 1.0~8.0 - 1.0 미만으로는
        감쇠 불가, 특정 채널만 낮춰 "덜 붉게/덜 파랗게" 만드는 용도로는 못 쓰고 나머지
        채널을 올려 상대적으로 맞추는 식으로 써야 함), 범위를 벗어난 값을 설정하면
        예외가 난다. 값 하나가 범위를 벗어났다고 화이트밸런스 전체를 꺼버리지 않도록
        각 채널을 유효 범위로 clamp한다 - docs/detection_notes.md 17차 참고.
        """
        if self._ipl is None:
            return
        try:
            gain = self._ipl.Gain()
            gain.SetRedGainValue(self._clamp_gain(settings.wb_gain_r, gain.RedGainMin(), gain.RedGainMax()))
            gain.SetGreenGainValue(self._clamp_gain(settings.wb_gain_g, gain.GreenGainMin(), gain.GreenGainMax()))
            gain.SetBlueGainValue(self._clamp_gain(settings.wb_gain_b, gain.BlueGainMin(), gain.BlueGainMax()))
        except Exception as exc:  # noqa: BLE001 - 화이트밸런스 없이 계속 진행
            print(f"[camera] 화이트밸런스 게인 설정 실패: {exc}")
            self._wb_gain = None
            return
        self._wb_gain = gain

    @staticmethod
    def _clamp_gain(value: float, minimum: float, maximum: float) -> float:
        if value < minimum or value > maximum:
            print(f"[camera] 화이트밸런스 게인 {value}가 유효 범위({minimum}~{maximum})를 벗어나 조정됨")
        return max(minimum, min(maximum, value))

    @property
    def is_running(self) -> bool:
        return self._running

    # ---- 내부 ----
    def _import_sdk(self):
        try:
            from ids_peak import ids_peak as peak  # type: ignore
            from ids_peak_ipl import ids_peak_ipl as ipl  # type: ignore
        except ImportError as exc:
            raise IdsPeakNotAvailableError(
                "ids_peak SDK를 임포트할 수 없습니다. IDS Software Suite를 설치하고 "
                "ids_peak/ids_peak_ipl Python 패키지를 사용 가능한 환경에서 실행하세요."
            ) from exc
        self._peak, self._ipl = peak, ipl
        return peak, ipl

    def _buffer_to_bgr(self, buffer, ipl) -> np.ndarray | None:
        try:
            # PyPI로 배포되는 최신 ids_peak_ipl(pip install ids-peak-ipl)에서는 이미지 생성이
            # 평평한 함수(Image_CreateFromSizeAndBuffer)가 아니라 Image 클래스의 메서드로
            # 바뀌었다(docs/windows_setup_guide.md 5-4 참고, 실기 설치 중 확인된 사항).
            raw_image = ipl.Image.CreateFromSizeAndBuffer(
                buffer.PixelFormat(), buffer.BasePtr(), buffer.Size(), buffer.Width(), buffer.Height()
            )
            self._apply_white_balance_gain(raw_image)
            color_image = raw_image.ConvertTo(ipl.PixelFormatName_BGR8)
            # 현재 ids_peak_ipl(1.17.x)의 Image 클래스에는 Data()가 없다 - 버퍼를 직접 numpy로
            # 받으려면 get_numpy_3D()(BGR8처럼 8bit/채널>1 포맷용)를 써야 한다.
            return color_image.get_numpy_3D().copy()
        except Exception as exc:  # noqa: BLE001
            print(f"[camera] 프레임 변환 실패: {exc}")
            return None

    def _apply_white_balance_gain(self, raw_image) -> None:
        """호스트 측 화이트밸런스 R/G/B 게인을 원본(raw) 이미지에 in-place로 적용.

        Gain.ProcessInPlace()는 컬러 변환 전(unpacked Bayer/Mono) 단계에서만 동작한다 -
        ConvertTo() 이후에는 이미 BGR로 변환되어 적용 불가(실측으로 확인).
        _wb_gain이 없으면(apply_settings() 미호출 등) 아무것도 하지 않는다.
        """
        if self._wb_gain is None:
            return
        pixel_format_name = raw_image.PixelFormat().PixelFormatName()
        if self._wb_gain.IsPixelFormatSupported(pixel_format_name):
            self._wb_gain.ProcessInPlace(raw_image)
