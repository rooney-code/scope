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
from typing import Any

import numpy as np

from core.camera.camera_service import CameraInfo, FrameCallback, ICameraService
from core.config.settings import CameraSettings


class IdsPeakNotAvailableError(RuntimeError):
    """ids_peak SDK를 임포트할 수 없을 때 발생 (미설치 환경)."""


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
        정확한 노드명은 실기에서 NodeMap 탐색으로 검증 필요(TBD 항목 다수).
        """
        nm = self._nodemap
        if nm is None:
            raise RuntimeError("카메라가 열려 있지 않습니다. open()을 먼저 호출하세요.")

        def _try_set(node_name: str, value: Any) -> None:
            try:
                nm.FindNode(node_name).SetValue(value)
            except Exception as exc:  # noqa: BLE001 - 현장 노드명 검증 전까지는 로그만
                print(f"[camera] 설정 실패(노드명 확인 필요): {node_name} = {value} ({exc})")

        # 1) 자동기능 끄기
        _try_set("ExposureAuto", "Off")
        _try_set("GainAuto", "Off")
        _try_set("BalanceWhiteAuto", "Off")
        _try_set("BlackLevelAuto", "Off")

        # 2) 수동값 적용
        _try_set("AcquisitionFrameRate", settings.frame_rate_fps)
        _try_set("ExposureTime", settings.exposure_time_us)
        _try_set("DeviceLinkThroughputLimit", settings.device_link_throughput_limit_bps)
        _try_set("Gain", settings.analog_gain)  # GainSelector=AnalogAll 전제, 실기 확인 필요
        _try_set("BlackLevel", settings.black_level)
        _try_set("BalanceRatioSelector", "Red")
        _try_set("BalanceRatio", settings.wb_gain_r)
        _try_set("BalanceRatioSelector", "Green")
        _try_set("BalanceRatio", settings.wb_gain_g)
        _try_set("BalanceRatioSelector", "Blue")
        _try_set("BalanceRatio", settings.wb_gain_b)

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
            arr = np.frombuffer(color_image.Data(), dtype=np.uint8)
            return arr.reshape(color_image.Height(), color_image.Width(), 3).copy()
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
