#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
"""RAMSES RF - a RAMSES-II protocol decoder & analyser."""

import logging
from random import randint, randrange
from typing import Dict, Optional

from .const import (
    _000C_DEVICE,
    _0005_ZONE,
    ATTR_HEAT_DEMAND,
    ATTR_RELAY_DEMAND,
    ATTR_SETPOINT,
    ATTR_TEMP,
    ATTR_WINDOW_OPEN,
    BOOST_TIMER,
    DEV_KLASS,
    DOMAIN_TYPE_MAP,
    FAN_MODE,
    NUL_DEVICE_ID,
    Discover,
    __dev_mode__,
)
from .entities import Entity, class_by_attr, discover_decorator
from .protocol import Command, CorruptStateError, Priority
from .protocol.address import NON_DEV_ADDR, id_to_address
from .protocol.command import FUNC, TIMEOUT
from .protocol.opentherm import (
    MSG_ID,
    MSG_NAME,
    MSG_TYPE,
    OPENTHERM_MESSAGES,
    PARAMS_MSG_IDS,
    SCHEMA_MSG_IDS,
    STATUS_MSG_IDS,
    VALUE,
)
from .protocol.ramses import CODE_ONLY_FROM_CTL, RAMSES_DEVICES
from .protocol.transport import PacketProtocolPort
from .schema import SZ_ALIAS, SZ_CLASS, SZ_DEVICE_ID, SZ_FAKED

from .protocol import I_, RP, RQ, W_  # noqa: F401, isort: skip
from .protocol import (  # noqa: F401, isort: skip
    _0001,
    _0002,
    _0004,
    _0005,
    _0006,
    _0008,
    _0009,
    _000A,
    _000C,
    _000E,
    _0016,
    _0100,
    _0150,
    _01D0,
    _01E9,
    _0404,
    _0418,
    _042F,
    _0B04,
    _1030,
    _1060,
    _1081,
    _1090,
    _1098,
    _10A0,
    _10B0,
    _10E0,
    _10E1,
    _1100,
    _1260,
    _1280,
    _1290,
    _1298,
    _12A0,
    _12B0,
    _12C0,
    _12C8,
    _12F0,
    _1300,
    _1F09,
    _1F41,
    _1FC9,
    _1FD0,
    _1FD4,
    _2249,
    _22C9,
    _22D0,
    _22D9,
    _22F1,
    _22F3,
    _2309,
    _2349,
    _2389,
    _2400,
    _2401,
    _2410,
    _2420,
    _2D49,
    _2E04,
    _30C9,
    _3120,
    _313F,
    _3150,
    _31D9,
    _31DA,
    _31E0,
    _3200,
    _3210,
    _3220,
    _3221,
    _3223,
    _3B00,
    _3EF0,
    _3EF1,
    _PUZZ,
)

DEFAULT_BDR_ID = "13:000730"
DEFAULT_EXT_ID = "17:000730"
DEFAULT_THM_ID = "03:000730"

DEV_MODE = __dev_mode__

_LOGGER = logging.getLogger(__name__)
if DEV_MODE:
    _LOGGER.setLevel(logging.DEBUG)


class DeviceBase(Entity):
    """The Device base class (good for a generic device)."""

    _DEV_KLASS = None
    _DEV_TYPES = tuple()  # TODO: needed?

    _STATE_ATTR = None

    def __init__(self, gwy, dev_addr, ctl=None, domain_id=None, **kwargs) -> None:
        _LOGGER.debug("Creating a Device: %s (%s)", dev_addr.id, self.__class__)
        super().__init__(gwy)

        self.id = dev_addr.id
        if self.id in gwy.device_by_id:
            raise LookupError(f"Duplicate device: {self.id}")

        gwy.device_by_id[self.id] = self
        gwy.devices.append(self)

        self._ctl = self._set_ctl(ctl) if ctl else None

        self._domain_id = domain_id
        self._parent = None

        self.addr = dev_addr
        self.type = dev_addr.type  # DEX  # TODO: remove this attr

        self.devices = []  # [self]
        self.device_by_id = {}  # {self.id: self}
        self._iz_controller = None

        self._alias = None
        self._faked = None
        if self.id in gwy._include:
            self._alias = gwy._include[self.id].get(SZ_ALIAS)

    def __repr__(self) -> str:
        if self._STATE_ATTR:
            return f"{self.id} ({self._domain_id}): {getattr(self, self._STATE_ATTR)}"
        return f"{self.id} ({self._domain_id})"

    def __str__(self) -> str:
        return self.id if self._klass is DEV_KLASS.DEV else f"{self.id} ({self._klass})"

    def __lt__(self, other) -> bool:
        if not hasattr(other, "id"):
            return NotImplemented
        return self.id < other.id

    def _start_discovery(self) -> None:

        delay = randint(10, 20)

        self._gwy._add_task(  # 10E0/1FC9, 3220 pkts
            self._discover, discover_flag=Discover.SCHEMA, delay=0, period=3600 * 24
        )
        self._gwy._add_task(
            self._discover, discover_flag=Discover.PARAMS, delay=delay, period=3600 * 6
        )
        self._gwy._add_task(
            self._discover, discover_flag=Discover.STATUS, delay=delay + 1, period=60
        )

    @discover_decorator
    def _discover(self, discover_flag=Discover.ALL) -> None:
        # sometimes, battery-powered devices will respond to an RQ (e.g. bind mode)

        if discover_flag & Discover.SCHEMA:
            self._make_cmd(_1FC9, retries=3)  # rf_bind

        if discover_flag & Discover.STATUS:
            self._make_cmd(_0016, retries=3)  # rf_check

    def _make_cmd(self, code, payload="00", **kwargs) -> None:
        super()._make_cmd(code, self.id, payload, **kwargs)

    def _set_ctl(self, ctl) -> None:  # self._ctl
        """Set the device's parent controller, after validating it."""

        if self._ctl is ctl:
            return
        if self._is_controller and not isinstance(self, UfhController):
            # HACK: UFC is/binds to a contlr
            return
        if self._ctl is not None:
            raise CorruptStateError(f"{self} changed controller: {self._ctl} to {ctl}")

        #  I --- 01:078710 --:------ 01:144246 1F09 003 FF04B5  # has been seen
        if not isinstance(ctl, Controller) and not ctl._is_controller:
            raise TypeError(f"Device {ctl} is not a controller")

        self._ctl = ctl
        ctl.device_by_id[self.id] = self
        ctl.devices.append(self)

        _LOGGER.debug("%s: controller now set to %s", self, ctl)
        return ctl

    def _handle_msg(self, msg) -> None:
        assert msg.src is self, f"msg inappropriately routed to {self}"
        super()._handle_msg(msg)

        if msg.verb != I_:  # or: if self._iz_controller is not None or...
            return

        if not self._iz_controller and msg.code in CODE_ONLY_FROM_CTL:
            if self._iz_controller is None:
                _LOGGER.info(f"{msg._pkt} # IS_CONTROLLER (00): is TRUE")
                self._make_tcs_controller(msg)
            elif self._iz_controller is False:  # TODO: raise CorruptStateError
                _LOGGER.error(f"{msg._pkt} # IS_CONTROLLER (01): was FALSE, now True")

    @property
    def has_battery(self) -> Optional[bool]:  # 1060
        """Return True if a device is battery powered (excludes battery-backup)."""

        return isinstance(self, BatteryState) or _1060 in self._msgz

    @property
    def _is_controller(self) -> Optional[bool]:

        if self._iz_controller is not None:
            return bool(self._iz_controller)  # True, False, or msg

        if self._ctl is not None:  # TODO: messy
            return self._ctl is self

        return False

    # @property
    # def _is_parent(self) -> bool:
    #     """Return True if other devices can bind to this device."""
    #     return self._klass in (DEV_KLASS.CTL, DEV_KLASS.PRG, DEV_KLASS.UFC)

    @property
    def _is_present(self) -> bool:
        """Try to exclude ghost devices (as caused by corrupt packet addresses)."""
        return any(
            m.src == self for m in self._msgs.values() if not m._expired
        )  # TODO: needs addressing

    @property
    def _klass(self) -> str:
        return self._DEV_KLASS

    def _make_tcs_controller(self, msg=None, **kwargs):  # CH/DHW
        """Create a TCS, and attach it to this controller."""
        from .systems import create_system  # HACK: needs sorting

        self._iz_controller = msg or True
        if self.type in ("01", "12", "22", "23", "34") and self._evo is None:  # DEX
            self._evo = create_system(self._gwy, self, **kwargs)

    @property
    def schema(self) -> dict:
        """Return the fixed attributes of the device (e.g. TODO)."""

        return {
            **(self._codes if DEV_MODE else {}),
            SZ_ALIAS: self._alias,
            # SZ_FAKED: self._faked,
            SZ_CLASS: self._klass,
        }

    @property
    def params(self):
        """Return the configurable attributes of the device."""
        return {}

    @property
    def status(self):
        """Return the state attributes of the device."""
        return {}


class DeviceInfo:  # 10E0

    RF_BIND = "rf_bind"
    DEVICE_INFO = "device_info"

    def _discover(self, discover_flag=Discover.ALL) -> None:
        if discover_flag & Discover.SCHEMA:
            if not self._msgs.get(_10E0) and (
                self._klass not in RAMSES_DEVICES
                or RP in RAMSES_DEVICES[self._klass].get(_10E0, {})
            ):
                self._make_cmd(_10E0, retries=3)

    @property
    def device_info(self) -> Optional[dict]:  # 10E0
        return self._msg_value(_10E0)

    @property
    def schema(self) -> dict:
        result = super().schema
        result.update({self.RF_BIND: self._msg_value(_1FC9)})
        if _10E0 in self._msgs or _10E0 in RAMSES_DEVICES.get(self._klass, []):
            result.update({self.DEVICE_INFO: self.device_info})
        return result


class Device(DeviceInfo, DeviceBase):
    """The Device base class - also used for unknown device types."""

    _DEV_KLASS = DEV_KLASS.DEV
    _DEV_TYPES = tuple()

    def _handle_msg(self, msg) -> None:
        super()._handle_msg(msg)

        if type(self) is Device and self.type == "30":  # self.__class__ is Device, DEX
            # TODO: the RFG codes need checking
            if msg.code in (_31D9, _31DA, _31E0) and msg.verb in (I_, RP):
                self.__class__ = HvacVentilator
            elif msg.code in (_0006, _0418, _3220) and msg.verb == RQ:
                self.__class__ = RfgGateway
            elif msg.code in (_313F,) and msg.verb == W_:
                self.__class__ = RfgGateway

        if not msg._gwy.config.enable_eavesdrop:
            return

        if (
            self._ctl is not None
            and "zone_idx" in msg.payload
            and msg.src.type != "01"  # TODO: DEX, should be: if controller
            # and msg.dst.type != "18"
        ):
            # TODO: is buggy - remove? how?
            self._set_parent(self._ctl._evo._get_zone(msg.payload["zone_idx"]))

    def _set_parent(self, parent, domain=None) -> None:  # self._parent
        """Set the device's parent zone, after validating it.

        There are three possible sources for the parent zone of a device:
        1. a 000C packet (from their controller) for actuators only
        2. a message.payload["zone_idx"]
        3. the sensor-matching algorithm for zone sensors only

        Devices don't have parents, rather: Zones have children; a mis-configured
        system could have a device as a child of two domains.
        """

        # NOTE: these imports are here to prevent circular references
        from .systems import System
        from .zones import DhwZone, Zone

        if self._parent is not None and self._parent is not parent:
            raise CorruptStateError(
                f"{self} changed parent: {self._parent} to {parent}, "
            )

        if isinstance(parent, Zone):
            if domain and domain != parent.idx:
                raise TypeError(f"{self}: domain must be {parent.idx}, not {domain}")
            domain = parent.idx

        elif isinstance(parent, DhwZone):  # usu. FA
            if domain not in ("F9", "FA"):  # may not be known if eavesdrop'd
                raise TypeError(f"{self}: domain must be F9 or FA, not {domain}")

        elif isinstance(parent, System):  # usu. FC
            if domain != "FC":  # was: not in ("F9", "FA", "FC", "HW"):
                raise TypeError(f"{self}: domain must be FC, not {domain}")

        else:
            raise TypeError(f"{self}: parent must be System, DHW or Zone, not {parent}")

        self._set_ctl(parent._ctl)
        self._parent = parent
        self._domain_id = domain

        if hasattr(parent, "devices") and self not in parent.devices:
            parent.devices.append(self)
            parent.device_by_id[self.id] = self

        _LOGGER.debug("Device %s: parent now set to %s", self, parent)
        return parent

    @property
    def zone(self) -> Optional[Entity]:  # should be: Optional[Zone]
        """Return the device's parent zone, if known."""

        return self._parent


class Actuator:  # 3EF0, 3EF1

    ACTUATOR_CYCLE = "actuator_cycle"
    ACTUATOR_ENABLED = "actuator_enabled"  # boolean
    ACTUATOR_STATE = "actuator_state"
    MODULATION_LEVEL = "modulation_level"  # percentage (0.0-1.0)

    def _handle_msg(self, msg) -> None:  # NOTE: active
        super()._handle_msg(msg)

        if isinstance(self, OtbGateway):
            return

        if (
            msg.code == _3EF0
            and msg.verb == I_
            and not self._faked
            and not self._gwy.config.disable_sending
        ):
            self._make_cmd(_3EF1, priority=Priority.LOW, retries=1)

    @property
    def _ch_active(self) -> Optional[bool]:  # 3EF0
        return self._msg_value(_3EF0, key="ch_active")

    @property
    def _ch_enabled(self) -> Optional[bool]:  # 3EF0
        return self._msg_value(_3EF0, key="ch_enabled")

    @property
    def _dhw_active(self) -> Optional[bool]:  # 3EF0
        return self._msg_value(_3EF0, key="dhw_active")

    @property
    def _flame_active(self) -> Optional[bool]:  # 3EF0
        return self._msg_value(_3EF0, key="flame_active")

    @property
    def _bit_3_7(self) -> Optional[bool]:  # 3EF0
        if flags := self._msg_value(_3EF0, key="_flags_3"):
            return flags[7]

    @property
    def _bit_6_6(self) -> Optional[bool]:  # 3EF0 ?dhw_enabled
        if flags := self._msg_value(_3EF0, key="_flags_6"):
            return flags[6]

    @property
    def actuator_cycle(self) -> Optional[dict]:  # 3EF1
        return self._msg_value(_3EF1)

    @property
    def actuator_state(self) -> Optional[dict]:  # 3EF0
        return self._msg_value(_3EF0)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.ACTUATOR_CYCLE: self.actuator_cycle,
            self.ACTUATOR_STATE: self.actuator_state,
        }


class BatteryState:  # 1060

    BATTERY_LOW = "battery_low"  # boolean
    BATTERY_STATE = "battery_state"  # percentage (0.0-1.0)

    @property
    def battery_low(self) -> Optional[bool]:  # 1060
        if self._faked:
            return False
        return self._msg_value(_1060, key=self.BATTERY_LOW)

    @property
    def battery_state(self) -> Optional[dict]:  # 1060
        if self._faked:
            return
        return self._msg_value(_1060)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.BATTERY_STATE: self.battery_state,
        }


class HeatDemand:  # 3150

    HEAT_DEMAND = ATTR_HEAT_DEMAND  # percentage valve open (0.0-1.0)

    @property
    def heat_demand(self) -> Optional[float]:  # 3150
        return self._msg_value(_3150, key=self.HEAT_DEMAND)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.HEAT_DEMAND: self.heat_demand,
        }


class Setpoint:  # 2309

    SETPOINT = ATTR_SETPOINT  # degrees Celsius

    @property
    def setpoint(self) -> Optional[float]:  # 2309
        return self._msg_value(_2309, key=self.SETPOINT)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.SETPOINT: self.setpoint,
        }


class Fakeable:
    def __init__(self, gwy, *args, **kwargs) -> None:
        super().__init__(gwy, *args, **kwargs)

        if self.id in gwy._include and gwy._include[self.id].get(SZ_FAKED):
            self._make_fake()

    def _bind(self):
        if not self._faked:
            raise RuntimeError(f"Can't bind {self} (Faking is not enabled)")

    def _make_fake(self, bind=None) -> Device:
        if not self._faked:
            self._faked = True
            self._gwy._include[self.id] = {SZ_FAKED: True}
            _LOGGER.warning(f"Faking now enabled for {self}")  # TODO: be info/debug
        if bind:
            self._bind()
        return self

    def _bind_waiting(self, code, idx="00", callback=None):
        """Wait for (listen for) a bind handshake."""

        # Bind waiting: BDR set to listen, CTL initiates handshake
        # 19:30:44.749 051  I --- 01:054173 --:------ 01:054173 1FC9 024 FC-0008-04D39D FC-3150-04D39D FB-3150-04D39D FC-1FC9-04D39D
        # 19:30:45.342 053  W --- 13:049798 01:054173 --:------ 1FC9 012 00-3EF0-34C286 00-3B00-34C286
        # 19:30:45.504 049  I --- 01:054173 13:049798 --:------ 1FC9 006 00-FFFF-04D39D

        _LOGGER.warning(f"Binding {self}: waiting for {code}")  # TODO: info/debug
        # SUPPORTED_CODES = (_0008,)

        def bind_confirm(msg, *args) -> None:
            """Process the 3rd/final packet of the handshake."""
            if not msg or msg.code != code:
                return
            self._1fc9_state == "confirm"

            # self._gwy._get_device(self, ctl_id=msg.payload[0][2])
            # self._ctl._evo._get_zone(msg.payload[0][0])._set_sensor(self)
            if callback:
                callback(msg)

        def bind_respond(msg, *args) -> None:
            """Process the 1st, and send the 2nd, packet of the handshake."""
            if not msg:
                return
            self._1fc9_state == "respond"

            # W should be retransmitted until receiving an I; idx is domain_id/zone_idx
            cmd = Command.put_bind(
                W_,
                code,
                self.id,
                idx=idx,
                dst_id=msg.src.id,
                callback={FUNC: bind_confirm, TIMEOUT: 3},
            )
            self._send_cmd(cmd)

        # assert code in SUPPORTED_CODES, f"Binding: {code} is not supported"
        self._1fc9_state = "waiting"

        self._gwy.msg_transport._add_callback(
            f"{_1FC9}|{I_}|{NUL_DEVICE_ID}", {FUNC: bind_respond, TIMEOUT: 300}
        )

    def _bind_request(self, code, callback=None):
        """Initiate a bind handshake: send the 1st packet of the handshake."""

        # Bind request: CTL set to listen, STA initiates handshake (note 3C09/2309)
        # 22:13:52.527 070  I --- 34:021943 --:------ 34:021943 1FC9 024 00-3C09-8855B7 00-30C9-8855B7 00-0008-8855B7 00-1FC9-8855B7
        # 22:13:52.540 052  W --- 01:145038 34:021943 --:------ 1FC9 006 00-2309-06368E
        # 22:13:52.572 071  I --- 34:021943 01:145038 --:------ 1FC9 006 00-2309-8855B7

        # Bind request: CTL set to listen, DHW sensor initiates handshake
        # 19:45:16.733 045  I --- 07:045960 --:------ 07:045960 1FC9 012 00-1260-1CB388 00-1FC9-1CB388
        # 19:45:16.896 045  W --- 01:054173 07:045960 --:------ 1FC9 006 00-10A0-04D39D
        # 19:45:16.919 045  I --- 07:045960 01:054173 --:------ 1FC9 006 00-1260-1CB388

        _LOGGER.warning(f"Binding {self}: requesting {code}")  # TODO: info/debug
        SUPPORTED_CODES = (_0002, _1260, _1290, _30C9)

        def bind_confirm(msg, *args) -> None:
            """Process the 2nd, and send the 3rd/final, packet of the handshake."""
            if not msg or msg.dst is not self:
                return

            self._1fc9_state == "confirm"

            cmd = Command.put_bind(I_, code, self.id, dst_id=msg.src.id)
            self._send_cmd(cmd)

            if callback:
                callback(msg)

        assert code in SUPPORTED_CODES, f"Binding: {code} is not supported"
        self._1fc9_state = "request"

        cmd = Command.put_bind(
            I_, code, self.id, callback={FUNC: bind_confirm, TIMEOUT: 3}
        )
        self._send_cmd(cmd)

    @property
    def schema(self) -> dict:
        return {
            **super().schema,
            SZ_FAKED: self._faked,
        }


class Weather(Fakeable):  # 0002 (fakeable)

    TEMPERATURE = ATTR_TEMP  # degrees Celsius

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if kwargs.get(SZ_FAKED) is True or _0002 in kwargs.get(SZ_FAKED, []):
            self._make_fake()

    def _bind(self):
        # A contrived (but proven viable) packet log...
        #  I --- 17:145039 --:------ 17:145039 1FC9 012 00-0002-46368F 00-1FC9-46368F
        #  W --- 01:054173 17:145039 --:------ 1FC9 006 03-2309-04D39D  # real CTL
        #  I --- 17:145039 01:054173 --:------ 1FC9 006 00-0002-46368F

        super()._bind()
        self._bind_request(_0002)

    @property
    def temperature(self) -> Optional[float]:  # 0002
        return self._msg_value(_0002, key=self.TEMPERATURE)

    @temperature.setter
    def temperature(self, value) -> None:  # 0002
        if not self._faked:
            raise RuntimeError(f"Can't set value for {self} (Faking is not enabled)")

        cmd = Command.put_outdoor_temp(self.id, value)
        # cmd = Command.put_zone_temp(
        #     self._gwy.hgi.id if self == self._gwy.hgi._faked_thm else self.id, value
        # )
        self._send_cmd(cmd)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.TEMPERATURE: self.temperature,
        }


class Temperature(Fakeable):  # 30C9 (fakeable)

    TEMPERATURE = ATTR_TEMP  # degrees Celsius

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if kwargs.get(SZ_FAKED) is True or _30C9 in kwargs.get(SZ_FAKED, []):
            self._make_fake()

    def _bind(self):
        # A contrived (but proven viable) packet log... offer should include 2309?
        #  I --- 34:145039 --:------ 34:145039 1FC9 012 00-30C9-8A368F 00-1FC9-8A368F
        #  W --- 01:054173 34:145039 --:------ 1FC9 006 03-2309-04D39D  # real CTL
        #  I --- 34:145039 01:054173 --:------ 1FC9 006 00-30C9-8A368F

        def callback(msg):
            msg.src._evo.zone_by_idx[msg.payload[0][0]]._set_sensor(self)
            self._1fc9_state == "bound"

        super()._bind()
        self._bind_request(_30C9, callback=callback)

    @property
    def temperature(self) -> Optional[float]:  # 30C9
        return self._msg_value(_30C9, key=self.TEMPERATURE)

    @temperature.setter
    def temperature(self, value) -> None:  # 30C9
        if not self._faked:
            raise RuntimeError(f"Can't set value for {self} (Faking is not enabled)")

        self._send_cmd(Command.put_sensor_temp(self.id, value))
        # lf._send_cmd(Command.get_zone_temp(self._ctl.id, self.zone.idx))

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.TEMPERATURE: self.temperature,
        }


class RelayDemand(Fakeable):  # 0008 (fakeable)

    RELAY_DEMAND = ATTR_RELAY_DEMAND  # percentage (0.0-1.0)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if kwargs.get(SZ_FAKED) is True or _3EF0 in kwargs.get(SZ_FAKED, []):
            self._make_fake()

    @discover_decorator
    def _discover(self, discover_flag=Discover.ALL) -> None:
        super()._discover(discover_flag=discover_flag)

        if discover_flag & Discover.STATUS and not self._faked:
            self._send_cmd(
                Command.get_relay_demand(self.id), priority=Priority.LOW, retries=1
            )

    def _handle_msg(self, msg) -> None:  # NOTE: active
        if msg.src.id == self.id:
            super()._handle_msg(msg)
            return

        if (
            self._gwy.config.disable_sending
            or not self._faked
            or self._domain_id is None
            or self._domain_id
            not in (v for k, v in msg.payload.items() if k in ("domain_id", "zone_idx"))
        ):
            return

        # TODO: handle relay_failsafe, reply to RQs
        if msg.code == _0008 and msg.verb == RQ:
            # 076  I --- 01:054173 --:------ 01:054173 0008 002 037C
            mod_level = msg.payload[self.RELAY_DEMAND]
            if mod_level is not None:
                mod_level = 1.0 if mod_level > 0 else 0

            cmd = Command.put_actuator_state(self.id, mod_level)
            qos = {"priority": Priority.HIGH, "retries": 3}
            [self._send_cmd(cmd, **qos) for _ in range(1)]

        elif msg.code == _0009:  # can only be I, from a controller
            pass

        elif msg.code == _3B00 and msg.verb == I_:
            pass

        elif msg.code == _3EF0 and msg.verb == I_:  # NOT RP, TODO: why????
            cmd = Command.get_relay_demand(self.id)
            self._send_cmd(cmd, priority=Priority.LOW, retries=1)

        elif msg.code == _3EF1 and msg.verb == RQ:  # NOTE: WIP
            mod_level = 1.0

            cmd = Command.put_actuator_cycle(self.id, msg.src.id, mod_level, 600, 600)
            qos = {"priority": Priority.HIGH, "retries": 3}
            [self._send_cmd(cmd, **qos) for _ in range(1)]

        else:
            raise

    def _bind(self):
        # A contrived (but proven viable) packet log...
        #  I --- 01:054173 --:------ 01:054173 1FC9 018 03-0008-04D39D FC-3B00-04D39D 03-1FC9-04D39D
        #  W --- 13:123456 01:054173 --:------ 1FC9 006 00-3EF0-35E240
        #  I --- 01:054173 13:123456 --:------ 1FC9 006 00-FFFF-04D39D

        super()._bind()
        self._bind_waiting(_3EF0)

    @property
    def relay_demand(self) -> Optional[float]:  # 0008
        return self._msg_value(_0008, key=self.RELAY_DEMAND)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.RELAY_DEMAND: self.relay_demand,
        }


class RfgGateway(DeviceInfo, DeviceBase):  # RFG (30:)
    """The RFG100 base class."""

    _DEV_KLASS = DEV_KLASS.RFG
    _DEV_TYPES = ("30",)


class HgiGateway(DeviceBase):  # HGI (18:), was GWY
    """The HGI80 base class."""

    _DEV_KLASS = DEV_KLASS.HGI
    _DEV_TYPES = ("18",)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._ctl = None
        self._domain_id = "FF"
        self._evo = None

        self._faked_bdr = None
        self._faked_ext = None
        self._faked_thm = None

        # self. _proc_schema(**kwargs)

    def _set_ctl(self, ctl) -> None:  # self._ctl
        """Set the device's parent controller, after validating it."""
        _LOGGER.debug("%s: can't (really) have a controller %s", self, ctl)

    def _proc_schema(self, schema) -> None:
        if schema.get("fake_bdr"):
            self._faked_bdr = self._gwy._get_device(self.id, class_="BDR", faked=True)

        if schema.get("fake_ext"):
            self._fake_ext = self._gwy._get_device(self.id, class_="BDR", faked=True)

        if schema.get("fake_thm"):
            self._fake_thm = self._gwy._get_device(self.id, class_="BDR", faked=True)

    @discover_decorator
    def _discover(self, discover_flag=Discover.ALL) -> None:
        # of no value for a HGI80-compatible device
        return

    def _handle_msg(self, msg) -> None:
        def fake_addrs(msg, faked_dev):
            msg.src == faked_dev if msg.src is self else self
            msg.dst == faked_dev if msg.dst is self else self
            return msg

        super()._handle_msg(msg)

        # the following is for aliased devices (not fully-faked devices)
        if msg.code in (_3EF0,) and self._faked_bdr:
            self._faked_bdr._handle_msg(fake_addrs(msg, self._faked_bdr))

        if msg.code in (_0002,) and self._faked_ext:
            self._faked_ext._handle_msg(fake_addrs(msg, self._faked_ext))

        if msg.code in (_30C9,) and self._faked_thm:
            self._faked_thm._handle_msg(fake_addrs(msg, self._faked_thm))

    def _create_fake_dev(self, dev_type, device_id) -> Device:
        if device_id[:2] != dev_type:
            raise TypeError(f"Invalid device ID {device_id} for type '{dev_type}:'")

        dev = self.device_by_id.get(device_id)
        if dev:
            _LOGGER.warning("Destroying %s", dev)
            if dev._ctl:
                del dev._ctl.device_by_id[dev.id]
                dev._ctl.devices.remove(dev)
                dev._ctl = None
            del self.device_by_id[dev.id]
            self.devices.remove(dev)
            dev = None

        dev = self._get_device(device_id)
        dev._make_fake(bind=True)
        return dev

    def create_fake_bdr(self, device_id=DEFAULT_BDR_ID) -> Device:
        """Bind a faked relay (BDR91A) to a controller (i.e. to a domain/zone).

        Will alias the gateway (as "13:000730"), or create a fully-faked 13:.

        HGI80s can only alias one device of a type (use_gateway), but evofw3-based
        gateways can also fully fake multiple devices of the same type.
        """
        if device_id in (self.id, None):
            device_id = DEFAULT_BDR_ID
        device = self._create_fake_dev("13", device_id=device_id)

        if device.id == DEFAULT_BDR_ID:
            self._faked_bdr = device
        return device

    def create_fake_ext(self, device_id=DEFAULT_EXT_ID) -> Device:
        """Bind a faked external sensor (???) to a controller.

        Will alias the gateway (as "17:000730"), or create a fully-faked 17:.

        HGI80s can only alias one device of a type (use_gateway), but evofw3-based
        gateways can also fully fake multiple devices of the same type.
        """

        if device_id in (self.id, None):
            device_id = DEFAULT_EXT_ID
        device = self._create_fake_dev("17", device_id=device_id)

        if device.id == DEFAULT_EXT_ID:
            self._faked_ext = device
        return device

    def create_fake_thm(self, device_id=DEFAULT_THM_ID) -> Device:
        """Bind a faked zone sensor (TR87RF) to a controller (i.e. to a zone).

        Will alias the gateway (as "03:000730"), or create a fully-faked 34:, albeit
        named "03:xxxxxx".

        HGI80s can only alias one device of a type (use_gateway), but evofw3-based
        gateways can also fully fake multiple devices of the same type.
        """
        if device_id in (self.id, None):
            device_id = DEFAULT_THM_ID
        device = self._create_fake_dev("03", device_id=device_id)

        if device.id == DEFAULT_THM_ID:
            self._faked_thm = device
        return device

    @property
    def schema(self):
        return {
            SZ_DEVICE_ID: self.id,
            "faked_bdr": self._faked_bdr and self._faked_bdr.id,
            "faked_ext": self._faked_ext and self._faked_ext.id,
            "faked_thm": self._faked_thm and self._faked_thm.id,
        }


class Controller(Device):  # CTL (01):
    """The Controller base class."""

    _DEV_KLASS = DEV_KLASS.CTL
    _DEV_TYPES = ("01",)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._ctl = self  # or args[1]
        self._domain_id = "FF"
        self._evo = None

        self._make_tcs_controller(**kwargs)

    def _handle_msg(self, msg) -> None:
        super()._handle_msg(msg)

        if msg.code == _000C:
            [
                self._gwy._get_device(d, ctl_id=self._ctl.id)
                for d in msg.payload["devices"]
            ]

        # Route any messages to their heating systems, TODO: create dev first?
        if self._evo:
            self._evo._handle_msg(msg)

    # @discover_decorator
    # def _discover(self, discover_flag=Discover.ALL) -> None:
    #     super()._discover(discover_flag=discover_flag)

    #     if discover_flag & Discover.SCHEMA:
    #         pass  # self._make_cmd(_0000, retries=3)


class Programmer(Controller):  # PRG (23):
    """The Controller base class."""

    _DEV_KLASS = DEV_KLASS.PRG
    _DEV_TYPES = ("23",)


class UfhController(Device):  # UFC (02):
    """The UFC class, the HCE80 that controls the UFH zones."""

    _DEV_KLASS = DEV_KLASS.UFC
    _DEV_TYPES = ("02",)

    HEAT_DEMAND = ATTR_HEAT_DEMAND

    _STATE_ATTR = "heat_demand"

    # 12:27:24.398 067  I --- 02:000921 --:------ 01:191718 3150 002 0360
    # 12:27:24.546 068  I --- 02:000921 --:------ 01:191718 3150 002 065A
    # 12:27:24.693 067  I --- 02:000921 --:------ 01:191718 3150 002 045C
    # 12:27:24.824 059  I --- 01:191718 --:------ 01:191718 3150 002 FC5C
    # 12:27:24.857 067  I --- 02:000921 --:------ 02:000921 3150 006 0060-015A-025C

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._circuits = {}
        self._setpoints = None
        self._heat_demand = None

        self._iz_controller = True

    @discover_decorator
    def _discover(self, discover_flag=Discover.ALL) -> None:
        super()._discover(discover_flag=discover_flag)

        if discover_flag & Discover.SCHEMA:
            for zone_type in (_0005_ZONE.ALL, _0005_ZONE.ALL_SENSOR, _0005_ZONE.UFH):
                # TODO: are all three needed?
                try:  # 0005: shows which channels are active - ?no use? (see above)
                    _ = self._msgz[_0005][RP][f"00{zone_type}"]
                except KeyError:
                    self._make_cmd(_0005, payload=f"00{zone_type}")

            for idx in range(8):
                try:  # _000C_DEVICE.UFH doesn't seem to work with all UFCs??
                    _ = self._msgz[_000C][RP][f"{idx:02X}{_000C_DEVICE.ALL}"]
                except KeyError:
                    self._make_cmd(_000C, payload=f"{idx:02X}{_000C_DEVICE.ALL}")

        # if discover_flag & Discover.STATUS:
        #     [  # 22C9: no answer
        #         self._make_cmd(_22C9, payload=f"{payload}")
        #         for payload in ("00", "0000", "01", "0100")
        #     ]

        #     [  # 3150: no answer
        #         self._make_cmd(_3150, payload=f"{zone_idx:02X}")
        #         for zone_idx in range(8)
        #     ]

    def _handle_msg(self, msg) -> None:
        super()._handle_msg(msg)

        if msg.code == _000C:
            assert "ufh_idx" in msg.payload, "wsdfh"
            if msg.payload["zone_id"] is not None:
                self._circuits[msg.payload["ufh_idx"]] = msg

            # if dev_ids := msg.payload["devices"]:
            #     if ctl := self._set_ctl(self._gwy._get_device(dev_ids[0])):
            #         if zone := ctl._evo.zone_by_idx.get(msg.payload["zone_id"]):
            #             zone.devices.append(self)
            #             zone.device_by_id[self.id] = self
            #             _LOGGER.debug("Device %s: added to Zone: %s", self, zone)

        elif msg.code == _22C9:
            if isinstance(msg.payload, list):
                self._setpoints = msg
            # else:
            #     pass  # update the self._circuits[]

        elif msg.code == _3150:
            if isinstance(msg.payload, list):
                self._heat_demands = msg
            elif "domain_id" in msg.payload:
                self._heat_demand = msg
            elif zone_idx := msg.payload.get("zone_idx"):
                if (
                    self._ctl
                    and self._ctl._evo
                    and (zone := self._ctl._evo.zone_by_idx.get(zone_idx))
                ):
                    zone._handle_msg(msg)
            # else:
            #     pass  # update the self._circuits[]

        # "0008|FA/FC", "22C9|array", "22D0|none", "3150|ZZ/array(/FC?)"

    @property
    def circuits(self) -> Optional[Dict]:  # 000C
        return {
            k: {"zone_idx": m.payload["zone_id"]} for k, m in self._circuits.items()
        }

    @property
    def zones(self) -> Optional[Dict]:  # 000C
        return {m.payload["zone_id"]: {"ufh_idx": k} for k, m in self._circuits.items()}

        # def fix(k):
        #     return "zone_idx" if k == "zone_id" else k

        # return [
        #     {fix(k): v for k, v in m.payload.items() if k in ("ufh_idx", "zone_id")}
        #     for m in self._circuits.values()
        # ]

    @property
    def heat_demand(self) -> Optional[float]:  # 3150
        if self._heat_demand:
            return self._heat_demand.payload[self.HEAT_DEMAND]

    @property
    def relay_demand(self) -> Optional[Dict]:  # 0008
        try:
            return self._msgs[_0008].payload[ATTR_RELAY_DEMAND]
        except KeyError:
            return

    @property
    def setpoints(self) -> Optional[Dict]:  # 22C9
        if self._setpoints is None:
            return

        return {
            c["ufh_idx"]: {"temp_high": c["temp_high"], "temp_low": c["temp_low"]}
            for c in self._setpoints.payload
        }

    @property  # id, type
    def schema(self) -> dict:
        return {
            **super().schema,
            "circuits": self.circuits,
        }

    @property  # setpoint, config, mode (not schedule)
    def params(self) -> dict:
        return {
            **super().params,
            "circuits": self.setpoints,
        }

    @property
    def status(self) -> dict:
        return {
            **super().status,
            ATTR_HEAT_DEMAND: self.heat_demand,
            ATTR_RELAY_DEMAND: self.relay_demand,
        }


class DhwSensor(BatteryState, Device):  # DHW (07): 10A0, 1260
    """The DHW class, such as a CS92."""

    _DEV_KLASS = DEV_KLASS.DHW
    _DEV_TYPES = ("07",)

    DHW_PARAMS = "dhw_params"
    TEMPERATURE = ATTR_TEMP

    _STATE_ATTR = "temperature"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._domain_id = "FA"

    def _handle_msg(self, msg) -> None:  # NOTE: active
        super()._handle_msg(msg)

        if msg.code == _1260 and self._ctl and not self._gwy.config.disable_sending:
            # device can be instantiated with a CTL
            self._send_cmd(Command.get_dhw_temp(self._ctl.id))

    @property
    def dhw_params(self) -> Optional[dict]:  # 10A0
        return self._msg_value(_10A0)

    @property
    def temperature(self) -> Optional[float]:  # 1260
        return self._msg_value(_1260, key=self.TEMPERATURE)

    @property
    def params(self) -> dict:
        return {
            **super().params,
            self.DHW_PARAMS: self.dhw_params,
        }

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.TEMPERATURE: self.temperature,
        }


class ExtSensor(Weather, Device):  # EXT: 17
    """The EXT class (external sensor), such as a HB85/HB95."""

    _DEV_KLASS = DEV_KLASS.EXT
    _DEV_TYPES = ("17",)

    # LUMINOSITY = "luminosity"  # lux
    # WINDSPEED = "windspeed"  # km/h

    _STATE_ATTR = "temperature"


class OtbGateway(Actuator, HeatDemand, Device):  # OTB (10): 3220 (22D9, others)
    """The OTB class, specifically an OpenTherm Bridge (R8810A Bridge)."""

    _DEV_KLASS = DEV_KLASS.OTB
    _DEV_TYPES = ("10",)

    # BOILER_SETPOINT = "boiler_setpoint"
    # OPENTHERM_STATUS = "opentherm_status"

    _STATE_ATTR = "rel_modulation_level"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._domain_id = "FC"

        self._msgz[_3220] = {RP: {}}
        self._opentherm_msg = self._msgz[_3220][RP]
        self._supported_msg = {}

    def _start_discovery(self) -> None:

        delay = randint(10, 20)

        self._gwy._add_task(  # 10E0/1FC9, 3220 pkts
            self._discover, discover_flag=Discover.SCHEMA, delay=0, period=3600 * 24
        )
        self._gwy._add_task(
            self._discover, discover_flag=Discover.PARAMS, delay=delay, period=3600
        )
        self._gwy._add_task(
            self._discover, discover_flag=Discover.STATUS, delay=delay + 1, period=60
        )

    @discover_decorator
    def _discover(self, discover_flag=Discover.ALL) -> None:
        # see: https://www.opentherm.eu/request-details/?post_ids=2944

        super()._discover(discover_flag=discover_flag)

        if discover_flag & Discover.SCHEMA:
            [
                self._send_cmd(Command.get_opentherm_data(self.id, m))
                for m in SCHEMA_MSG_IDS  # From OT v2.2: version numbers
                if self._supported_msg.get(m) is not False
                and (not self._opentherm_msg.get(m) or self._opentherm_msg[m]._expired)
            ]

        if discover_flag & Discover.PARAMS:  # and not DEV_MODE:
            for code in (_10A0,):  # dhw_setpoint
                self._send_cmd(Command(RQ, code, "00", self.id))

        if discover_flag & Discover.PARAMS:
            [
                self._send_cmd(Command.get_opentherm_data(self.id, m))
                for m in PARAMS_MSG_IDS
                if self._supported_msg.get(m) is not False
                and (not self._opentherm_msg.get(m) or self._opentherm_msg[m]._expired)
            ]

        if discover_flag & Discover.STATUS:
            [
                self._send_cmd(Command.get_opentherm_data(self.id, m, retries=0))
                for m in STATUS_MSG_IDS
                if self._supported_msg.get(m) is not False
                # nd (not self._opentherm_msg.get(m) or self._opentherm_msg[m]._expired)
            ]  # TODO: add expired

            # NOTE: No need to send periodic RQ/3EF1s to an OTB, can use RQ/3220/11s?
            self._send_cmd(Command(RQ, _3EF0, "00", self.id))  # CTLs dont RP to RQ/3EF0

        if discover_flag & Discover.STATUS:
            # these are WIP
            for code in (
                _2401,  # WIP - modulation_level + flags?
                _3221,  # R8810A/20A
                _3223,  # R8810A/20A
            ):
                self._send_cmd(Command(RQ, code, "00", self.id))

            # TODO: these appear fixed in value - to test against BDR91T
            if DEV_MODE:
                for code in (
                    _0150,  # payload always "000000", R8820A only?
                    _1098,  # payload always "00C8",   R8820A only?
                    _10B0,  # payload always "0000",   R8820A only?
                    _1FD0,  # payload always "0000000000000000"
                    _2400,  # payload always "0000000F"
                    _2410,  # payload always "000000000000000000000000010000000100000C"
                    _2420,  # payload always "0000001000000...
                ):
                    self._send_cmd(Command(RQ, code, "00", self.id))

    def _handle_msg(self, msg) -> None:
        super()._handle_msg(msg)

        if msg.code != _3220:
            return

        msg_id = f"{msg.payload[MSG_ID]:02X}"

        if msg._pkt.payload[4:] == "121980" or msg._pkt.payload[6:] == "47AB":
            if msg_id not in self._supported_msg:
                self._supported_msg[msg_id] = None

            elif self._supported_msg[msg_id] is None:
                self._supported_msg[msg_id] = False
                _LOGGER.warning(
                    f"{msg._pkt} < OpenTherm: deprecating msg_id "
                    f"0x{msg_id}: it appears unsupported",
                )

        else:
            self._supported_msg[msg_id] = msg.payload[MSG_TYPE] not in (
                "Data-Invalid",
                "Unknown-DataId",
                "-reserved-",
            )

        # TODO: this is development code - will be rationalised, eventually
        if DEV_MODE:
            code = {
                "01": _22D9,  # boiler_setpoint
                "11": _3EF1,  # rel_modulation_level
                "12": _1300,  # ch_water_pressure
                "13": _12F0,  # dhw_flow_rate
                "19": _3200,  # boiler_output_temp (checked)
                "1A": _1260,  # dhw_temp (checked)
                "1B": _1290,  # outside_temp
                "1C": _3210,  # boiler_return_temp (checked)
                "38": _10A0,  # dhw_setpoint
                "39": _1081,  # ch_max_setpoint
            }.get(msg_id)
        elif randrange(6):
            code = {
                "01": _22D9,  # boiler_setpoint
                "11": _3EF1,  # rel_modulation_level
                "12": _1300,  # ch_water_pressure
                "13": _12F0,  # dhw_flow_rate
                "1B": _1290,  # outside_temp
                "38": _10A0,  # dhw_setpoint
                "39": _1081,  # ch_max_setpoint
            }.get(msg_id)
        else:
            code = None
        if code:
            self._send_cmd(Command(RQ, code, "00", self.id))

    @staticmethod
    def _ot_msg_name(msg) -> str:
        return (
            msg.payload[MSG_NAME]
            if isinstance(msg.payload[MSG_NAME], str)
            else f"{msg.payload[MSG_ID]:02X}"
        )

    def _ot_msg_value(self, msg_id) -> Optional[float]:
        if (
            (msg := self._opentherm_msg.get(msg_id))
            and self._supported_msg[msg_id]
            and not msg._expired
        ):
            return msg.payload.get(VALUE)

    @property
    def _bit_2_4(self) -> Optional[bool]:  # 2401
        if flags := self._msg_value(_2401, key="_flags_2"):
            return flags[4]

    @property
    def _bit_2_5(self) -> Optional[bool]:  # 2401
        if flags := self._msg_value(_2401, key="_flags_2"):
            return flags[5]

    @property
    def _bit_2_6(self) -> Optional[bool]:  # 2401
        if flags := self._msg_value(_2401, key="_flags_2"):
            return flags[6]

    @property
    def _bit_2_7(self) -> Optional[bool]:  # 2401
        if flags := self._msg_value(_2401, key="_flags_2"):
            return flags[7]

    @property
    def boiler_output_temp(self) -> Optional[float]:  # 3220/19 (3200)
        return self._ot_msg_value("19")

    @property
    def _boiler_output_temp(self) -> Optional[float]:  # 3200 (3220/19)
        return self._msg_value(_3200, key="temperature")

    @property
    def boiler_return_temp(self) -> Optional[float]:  # 3220/1C (3210)
        return self._ot_msg_value("1C")

    @property
    def _boiler_return_temp(self) -> Optional[float]:  # 3210 (3220/1C)
        return self._msg_value(_3210, key="temperature")

    @property
    def boiler_setpoint(self) -> Optional[float]:  # 3220/01 (22D9)
        return self._ot_msg_value("01")

    @property
    def _boiler_setpoint(self) -> Optional[float]:  # 22D9
        return self._msg_value(_22D9, key="setpoint")

    @property
    def ch_max_setpoint(self) -> Optional[float]:  # 3220/39 (1081)
        return self._ot_msg_value("39")

    @property
    def _ch_max_setpoint(self) -> Optional[float]:  # 1081
        return self._msg_value(_1081, key="setpoint")

    @property
    def _ch_setpoint(self) -> Optional[bool]:  # 3EF0
        return self._msg_value(_3EF0, key="ch_setpoint")

    @property
    def ch_water_pressure(self) -> Optional[float]:  # 3220/12 (1300)
        return self._ot_msg_value("12")

    @property
    def _ch_water_pressure(self) -> Optional[float]:  # 1300
        result = self._msg_value(_1300, key="pressure")
        return None if result == 25.5 else result  # HACK: to make more rigourous

    @property
    def dhw_flow_rate(self) -> Optional[float]:  # 3220/13 (12F0)
        return self._ot_msg_value("13")

    @property
    def _dhw_flow_rate(self) -> Optional[float]:  # 12F0
        return self._msg_value(_12F0, key="dhw_flow_rate")

    @property
    def dhw_setpoint(self) -> Optional[float]:  # 10A0
        return self._ot_msg_value("38")

    @property
    def _dhw_setpoint(self) -> Optional[float]:  # 3220/38 (10A0)
        return self._msg_value(_1300, key="setpoint")

    @property
    def dhw_temp(self) -> Optional[float]:  # 3220/1A (1260)
        return self._ot_msg_value("1A")

    @property
    def _dhw_temp(self) -> Optional[float]:  # 1260
        return self._msg_value(_1260, key="temperature")

    @property
    def outside_temp(self) -> Optional[float]:  # 3220/1B (1290)
        return self._ot_msg_value("1B")

    @property
    def _outside_temp(self) -> Optional[float]:  # 1290
        return self._msg_value(_1290, key="temperature")

    @property
    def _percent(self) -> Optional[float]:  # 2401
        return self._msg_value(_2401, key="_percent_3")

    @property
    def rel_modulation_level(self) -> Optional[float]:  # 3220/11 (3EFx)
        """Return the relative modulation level from OpenTherm."""
        return self._ot_msg_value("11")

    @property
    def _rel_modulation_level(self) -> Optional[float]:  # 3EF0/3EF1
        """Return the relative modulation level from RAMSES_II."""
        return self._msg_value((_3EF0, _3EF1), key=self.MODULATION_LEVEL)

    @property
    def _max_rel_modulation(self) -> Optional[float]:  # 3EF0
        return self._msg_value(_3EF0, key="max_rel_modulation")

    @property
    def ch_active(self) -> Optional[bool]:  # 3220/00
        if flags := self._ot_msg_value("00"):
            return flags[8 + 1]

    @property
    def ch_enabled(self) -> Optional[bool]:  # 3220/00
        if flags := self._ot_msg_value("00"):
            return flags[0]

    @property
    def dhw_active(self) -> Optional[bool]:  # 3220/00
        if flags := self._ot_msg_value("00"):
            return flags[8 + 2]

    @property
    def dhw_enabled(self) -> Optional[bool]:  # 3220/00
        if flags := self._ot_msg_value("00"):
            return flags[1]

    @property
    def flame_active(self) -> Optional[bool]:  # 3220/00 (flame_on)
        if flags := self._ot_msg_value("00"):
            return flags[8 + 3]

    @property
    def cooling_active(self) -> Optional[bool]:  # 3220/00
        if flags := self._ot_msg_value("00"):
            return flags[8 + 4]

    @property
    def cooling_enabled(self) -> Optional[bool]:  # 3220/00
        if flags := self._ot_msg_value("00"):
            return flags[2]

    @property
    def fault_present(self) -> Optional[bool]:  # 3220/00
        if flags := self._ot_msg_value("00"):
            return flags[8]

    def opentherm_schema(self) -> dict:
        result = {
            self._ot_msg_name(v): v.payload
            for k, v in self._opentherm_msg.items()
            if self._supported_msg.get(int(k, 16)) and int(k, 16) in SCHEMA_MSG_IDS
        }
        return {
            m: {k: v for k, v in p.items() if k.startswith(VALUE)}
            for m, p in result.items()
        }

    @property
    def opentherm_params(self) -> dict:
        result = {
            self._ot_msg_name(v): v.payload
            for k, v in self._opentherm_msg.items()
            if self._supported_msg.get(int(k, 16)) and int(k, 16) in PARAMS_MSG_IDS
        }
        return {
            m: {k: v for k, v in p.items() if k.startswith(VALUE)}
            for m, p in result.items()
        }

    @property
    def opentherm_status(self) -> dict:
        return {
            "boiler_output_temp": self.boiler_output_temp,
            "boiler_return_temp": self.boiler_return_temp,
            "boiler_setpoint": self.boiler_setpoint,
            "ch_max_setpoint": self.ch_max_setpoint,
            "ch_water_pressure": self.ch_water_pressure,
            "dhw_flow_rate": self.dhw_flow_rate,
            "dhw_setpoint": self.dhw_setpoint,
            "dhw_temp": self.dhw_temp,
            "outside_temp": self.outside_temp,
            "rel_modulation_level": self.rel_modulation_level,
        }

    @property
    def ramses_params(self) -> dict:
        return {
            "ch_max_setpoint": self._ch_max_setpoint,
            "dhw_setpoint": self._dhw_setpoint,
            "max_rel_modulation": self._max_rel_modulation,
        }

    @property
    def ramses_status(self) -> dict:
        return {
            "boiler_setpoint": self._boiler_setpoint,
            "ch_setpoint": self._ch_setpoint,
            "ch_water_pressure": self._ch_water_pressure,
            "dhw_temp": self._dhw_temp,
            "outside_temp": self._outside_temp,
            "rel_modulation_level": self._rel_modulation_level,
            "ch_active": self._ch_active,
            "ch_enabled": self._ch_enabled,
            "dhw_active": self._dhw_active,
            "flame_active": self._flame_active,
        }

    @property
    def schema(self) -> dict:
        return {
            **super().schema,
            "known_msg_ids": {
                k: OPENTHERM_MESSAGES[int(k, 16)].get("var", k)
                for k, v in sorted(self._supported_msg.items())
                if v
            },
            "opentherm_schema": self.opentherm_schema,
        }

    @property
    def params(self) -> dict:
        return {
            **super().params,
            "opentherm_params": self.opentherm_params,
            "supported_msgs": dict(sorted(self._supported_msg.items())),
        }

    @property
    def status(self) -> dict:
        return {
            **super().status,  # incl. actuator_cycle, actuator_state
            "opentherm_status": self.opentherm_status,
            "ramses_status": self.ramses_status,
        }


class Thermostat(BatteryState, Setpoint, Temperature, Device):  # THM (..):
    """The THM/STA class, such as a TR87RF."""

    _DEV_KLASS = DEV_KLASS.THM
    _DEV_TYPES = ("03", "12", "22", "34")

    _STATE_ATTR = "temperature"

    def _handle_msg(self, msg) -> None:
        super()._handle_msg(msg)

        if msg.verb != I_:  # or: if self._iz_controller is not None or...
            return

        # if self._iz_controller is not None:  # TODO: put back in when confident
        #     return

        # NOTE: this has only been tested on a 12:, does it work for a 34: too?
        if all(
            (
                msg._addrs[0] is self.addr,
                msg._addrs[1] is NON_DEV_ADDR,
                msg._addrs[2] is self.addr,
            )
        ):
            if self._iz_controller is None:
                # _LOGGER.info(f"{msg._pkt} # IS_CONTROLLER (10): is FALSE")
                self._iz_controller = False
            elif self._iz_controller:  # TODO: raise CorruptStateError
                _LOGGER.error(f"{msg._pkt} # IS_CONTROLLER (11): was TRUE, now False")

            if msg.code in CODE_ONLY_FROM_CTL:  # TODO: raise CorruptPktError
                _LOGGER.error(f"{msg._pkt} # IS_CONTROLLER (12); is CORRUPT PKT")

        elif all(
            (
                msg._addrs[0] is NON_DEV_ADDR,
                msg._addrs[1] is NON_DEV_ADDR,
                msg._addrs[2] is self.addr,
            )
        ):
            if self._iz_controller is None:
                # _LOGGER.info(f"{msg._pkt} # IS_CONTROLLER (20): is TRUE")
                self._iz_controller = msg
                self._make_tcs_controller(msg)
            elif self._iz_controller is False:  # TODO: raise CorruptStateError
                _LOGGER.error(f"{msg._pkt} # IS_CONTROLLER (21): was FALSE, now True")


class BdrSwitch(Actuator, RelayDemand, Device):  # BDR (13):
    """The BDR class, such as a BDR91.

    BDR91s can be used in six disctinct modes, including:
    - x2 boiler controller (FC/TPI): either traditional, or newer heat pump-aware
    - x1 electric heat zones (0x/ELE)
    - x1 zone valve zones (0x/VAL)
    - x2 DHW thingys (F9/DHW, FA/DHW)
    """

    _DEV_KLASS = DEV_KLASS.BDR
    _DEV_TYPES = ("13",)

    ACTIVE = "active"
    TPI_PARAMS = "tpi_params"

    _STATE_ATTR = "active"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # if kwargs.get("domain_id") == "FC":  # TODO: F9/FA/FC, zone_idx
        #     self._ctl._set_htg_control(self)

    @discover_decorator
    def _discover(self, discover_flag=Discover.ALL) -> None:
        """Discover BDRs.

        The BDRs have one of six roles:
         - heater relay *or* a heat pump relay (alternative to an OTB)
         - DHW hot water valve *or* DHW heating valve
         - Zones: Electric relay *or* Zone valve relay

        They all seem to respond thus (TODO: heat pump/zone valve relay):
         - all BDR91As will (erractically) RP to these RQs
             0016, 1FC9 & 0008, 1100, 3EF1
         - all BDR91As will *not* RP to these RQs
             0009, 10E0, 3B00, 3EF0
         - a BDR91A will *periodically* send an I/3B00/00C8 if it is the heater relay
        """

        super()._discover(discover_flag=discover_flag)

        if discover_flag & Discover.PARAMS and not self._faked:
            self._send_cmd(Command.get_tpi_params(self.id))  # or: self._ctl.id

        if discover_flag & Discover.STATUS and not self._faked:
            # NOTE: 13: wont RP to an RQ/3EF0
            self._make_cmd(_3EF1)

    def _handle_msg(self, msg) -> None:
        super()._handle_msg(msg)

        # if msg.code == _1FC9 and msg.verb == RP:
        #     pass  # only a heater_relay will have 3B00

        # elif msg.code == _3B00 and msg.verb == I_:
        #     pass  # only a heater_relay will I/3B00
        #     # for code in (_0008, _3EF1):
        #     #     self._make_cmd(code, delay=1)

    @property
    def active(self) -> Optional[bool]:  # 3EF0, 3EF1
        """Return the actuator's current state."""
        result = self._msg_value((_3EF0, _3EF1), key=self.MODULATION_LEVEL)
        return None if result is None else bool(result)

    @property
    def role(self) -> Optional[str]:
        """Return the role of the BDR91A (there are six possibilities)."""

        if self._domain_id in DOMAIN_TYPE_MAP:
            return DOMAIN_TYPE_MAP[self._domain_id]
        elif self._parent:
            return self._parent.heating_type  # TODO: only applies to zones

        # if _3B00 in self._msgs and self._msgs[_3B00].verb == I_:
        #     self._is_tpi = True
        # if _1FC9 in self._msgs and self._msgs[_1FC9].verb == RP:
        #     if _3B00 in self._msgs[_1FC9].raw_payload:
        #         self._is_tpi = True

    @property
    def tpi_params(self) -> Optional[dict]:  # 1100
        return self._msg_value(_1100)

    @property
    def schema(self) -> dict:
        return {
            **super().schema,
            "role": self.role,
        }

    @property
    def params(self) -> dict:
        return {
            **super().params,
            self.TPI_PARAMS: self.tpi_params,
        }

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.ACTIVE: self.active,
        }


class TrvActuator(BatteryState, HeatDemand, Setpoint, Temperature, Device):  # TRV (04):
    """The TRV class, such as a HR92."""

    _DEV_KLASS = DEV_KLASS.TRV
    _DEV_TYPES = ("00", "04")  # TODO: keep 00?

    WINDOW_OPEN = ATTR_WINDOW_OPEN  # boolean

    _STATE_ATTR = "heat_demand"

    @property
    def heat_demand(self) -> Optional[float]:  # 3150
        if (heat_demand := super().heat_demand) is None:
            if self._msg_value(_3150) is None and self.setpoint is False:
                return 0  # instead of None (no 3150s sent when setpoint is False)
        return heat_demand

    @property
    def window_open(self) -> Optional[bool]:  # 12B0
        return self._msg_value(_12B0, key=self.WINDOW_OPEN)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.WINDOW_OPEN: self.window_open,
        }


class HvacHumidity(BatteryState, Device):  # HUM (32) I/12A0
    """The Sensor class for a humidity sensor.

    The cardinal code is 12A0.
    """

    _DEV_KLASS = DEV_KLASS.HUM
    _DEV_TYPES = tuple()  # ("32",)

    REL_HUMIDITY = "indoor_humidity"  # percentage (0.0-1.0)
    TEMPERATURE = "temperature"  # celsius
    DEWPOINT_TEMP = "dewpoint_temp"  # celsius

    @property
    def indoor_humidity(self) -> Optional[float]:
        return self._msg_value(_12A0, key=self.REL_HUMIDITY)

    @property
    def temperature(self) -> Optional[float]:
        return self._msg_value(_12A0, key=self.TEMPERATURE)

    @property
    def dewpoint_temp(self) -> Optional[float]:
        return self._msg_value(_12A0, key=self.DEWPOINT_TEMP)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            self.REL_HUMIDITY: self.relative_humidity,
            self.TEMPERATURE: self.temperature,
            self.DEWPOINT_TEMP: self.dewpoint_temp,
        }


class HvacCarbonDioxide(Device):  # HUM (32) I/1298
    """The Sensor class for a CO2 sensor.

    The cardinal code is 1298.
    """

    _DEV_KLASS = DEV_KLASS.CO2
    _DEV_TYPES = tuple()  # ("32",)

    @property
    def co2_level(self) -> Optional[float]:
        return self._msg_value(_1298, key="co2_level")

    @property
    def status(self) -> dict:
        return {
            **super().status,
            "co2_level": self.co2_level,
        }


class HvacSwitch(BatteryState, Device):  # SWI (39): I/22F[13]
    """The FAN (switch) class, such as a 4-way switch.

    The cardinal codes are 22F1, 22F3.
    """

    # every /15
    # RQ --- 32:166025 30:079129 --:------ 31DA 001 21
    # RP --- 30:079129 32:166025 --:------ 31DA 029 21EF00026036EF7FFF7FFF7FFF7FFF0002EF18FFFF000000EF7FFF7FFF

    _DEV_KLASS = DEV_KLASS.SWI
    _DEV_TYPES = tuple()  # ("39",)

    @property
    def fan_mode(self) -> Optional[str]:
        return self._msg_value(_22F1, key=FAN_MODE)

    @property
    def boost_timer(self) -> Optional[int]:
        return self._msg_value(_22F3, key=BOOST_TIMER)

    @property
    def status(self) -> dict:
        return {
            **super().status,
            FAN_MODE: self.fan_mode,
            BOOST_TIMER: self.boost_timer,
        }


class HvacVentilator(Device):  # FAN (20/37): RP/31DA, I/31D[9A]
    """The Ventilation class.

    The cardinal code are 31D9, 31DA.  Signature is RP/31DA.
    """

    # Itho Daalderop (NL)
    # Heatrae Sadia (UK)
    # Nuaire (UK), e.g. DRI-ECO-PIV

    # every /30
    # 30:079129 --:------ 30:079129 31D9 017 2100FF0000000000000000000000000000

    _DEV_KLASS = DEV_KLASS.FAN
    _DEV_TYPES = tuple()  # ("20", "37")

    @property
    def boost_timer(self) -> Optional[int]:
        return self._msg_value(_31DA, key="remaining_time")

    @property
    def co2_level(self) -> Optional[int]:
        return self._msg_value(_31DA, key="co2_level")

    @property
    def fan_rate(self) -> Optional[float]:
        return self._msg_value((_31D9, _31DA), key="exhaust_fan_speed")

    @property
    def indoor_humidity(self) -> Optional[float]:
        return self._msg_value(_31DA, key="indoor_humidity")

    @property
    def status(self) -> dict:
        return {
            **super().status,
            "exhaust_fan_speed": self.fan_rate,
            **(
                {
                    k: v
                    for k, v in self._msgs[_31D9].payload.items()
                    if k != "exhaust_fan_speed"
                }
                if _31D9 in self._msgs
                else {}
            ),
            **(
                {
                    k: v
                    for k, v in self._msgs[_31DA].payload.items()
                    if k != "exhaust_fan_speed"
                }
                if _31DA in self._msgs
                else {}
            ),
        }


_DEV_BY_KLASS = class_by_attr(__name__, "_DEV_KLASS")  # e.g. "CTL": Controller

_DEV_TYPE_TO_KLASS = {  # TODO: *remove*
    None: DEV_KLASS.DEV,  # a generic, promotable device
    # "00": DEV_KLASS.TRV,
    "01": DEV_KLASS.CTL,
    "02": DEV_KLASS.UFC,
    "03": DEV_KLASS.THM,
    "04": DEV_KLASS.TRV,
    "07": DEV_KLASS.DHW,
    "10": DEV_KLASS.OTB,
    "12": DEV_KLASS.THM,  # 12: can act like a DEV_KLASS.PRG
    "13": DEV_KLASS.BDR,
    "17": DEV_KLASS.EXT,
    "18": DEV_KLASS.HGI,
    "22": DEV_KLASS.THM,  # 22: can act like a DEV_KLASS.PRG
    "23": DEV_KLASS.PRG,
    "30": DEV_KLASS.RFG,  # either: RFG/FAN
    "34": DEV_KLASS.THM,
}  # these are the default device classes for Honeywell (non-HVAC) types


def create_device(gwy, dev_id: str, klass=None, **kwargs) -> Device:
    """Create a device, and optionally perform discovery & start polling."""

    if klass is None:
        klass = _DEV_TYPE_TO_KLASS.get(dev_id[:2], DEV_KLASS.DEV)  # DEX

    device = _DEV_BY_KLASS.get(klass, Device)(gwy, id_to_address(dev_id), **kwargs)

    if isinstance(gwy.pkt_protocol, PacketProtocolPort):
        device._start_discovery()

    return device


_OUT_DEV_KLASS_BY_SIGNATURE = {
    "FAN": ((RP, _31DA), (I_, _31D9), (I_, _31DA)),
    "SWI": ((I_, _22F1), (I_, _22F3)),
    "HUM": ((I_, _12A0)),
    "CO2": ((I_, _1298)),
}

if DEV_MODE:
    # check that each entity with a non-null _STATE_ATTR has that attr
    [
        d
        for d in class_by_attr(__name__, "_STATE_ATTR").values()
        if d._STATE_ATTR and getattr(d, d._STATE_ATTR)
    ]
