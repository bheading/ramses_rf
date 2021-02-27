#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
"""Evohome RF - discovery scripts."""

import asyncio
import json
import logging
import re
from typing import Any, List

from .command import Command, Priority
from .const import (
    _dev_mode_,
    ALL_DEVICE_ID,
    CODE_SCHEMA,
    DEVICE_TABLE,
    HGI_DEV_ADDR,
    NON_DEV_ADDR,
    Address,
)
from .exceptions import ExpiredCallbackError
from .helpers import extract_addrs
from .ramses import RAMSES_CODES

EXECUTE_CMD = "execute_cmd"
GET_FAULTS = "get_faults"
GET_SCHED = "get_schedule"
SET_SCHED = "set_schedule"

SCAN_DISC = "scan_disc"
SCAN_FULL = "scan_full"
SCAN_HARD = "scan_hard"
SCAN_XXXX = "scan_xxxx"

DEVICE_ID_REGEX = re.compile(ALL_DEVICE_ID)


DEV_MODE = _dev_mode_

_LOGGER = logging.getLogger(__name__)
if DEV_MODE:
    _LOGGER.setLevel(logging.DEBUG)


def spawn_execute_cmd(gwy, **kwargs):
    if kwargs.get(EXECUTE_CMD):  # e.g. "RQ 01:145038 1F09 00"
        cmd = kwargs[EXECUTE_CMD].split()

        verb = cmd.pop(0)
        seqn = "---" if DEVICE_ID_REGEX.match(cmd[0]) else cmd.pop(0)

        payload = cmd.pop()
        code = cmd.pop()

        if len(cmd) == 1:
            addrs = extract_addrs(f"{HGI_DEV_ADDR.id} {cmd[0]} {NON_DEV_ADDR.id}")[2]
        elif len(cmd) == 2:
            addrs = extract_addrs(f"{cmd[0]} {cmd[1]} {NON_DEV_ADDR.id}")[2]
        elif len(cmd) == 3:
            addrs = extract_addrs(f"{cmd[0]} {cmd[1]} {cmd[2]}")[2]
        else:
            _LOGGER.warning(
                "Execute: Command is invalid, and will be ignored: '%s'",
                kwargs[EXECUTE_CMD],
            )
            return

        qos = {"priority": Priority.HIGH, "retries": 3}
        try:
            cmd = Command.packet(
                verb, seqn, *[a.id for a in addrs], code, payload, qos=qos
            )
        except ValueError as err:
            _LOGGER.warning(
                "Execute: Command is invalid, and will be ignored: '%s' (%s)",
                kwargs[EXECUTE_CMD],
                err,
            )
        else:
            gwy.send_cmd(cmd)


def spawn_monitor_scripts(gwy, **kwargs) -> List[Any]:
    tasks = []

    if kwargs.get(EXECUTE_CMD):
        spawn_execute_cmd(gwy, **kwargs)  # TODO: wrap in a try?

    if kwargs.get("poll_devices"):
        tasks += [poll_device(gwy, d) for d in kwargs["poll_devices"]]

    gwy._tasks.extend(tasks)
    return tasks


def spawn_execute_scripts(gwy, **kwargs) -> List[Any]:

    # this is to ensure the gateway interface has fully woken
    if not kwargs.get(EXECUTE_CMD) and gwy._include:
        dev_id = next(iter(gwy._include))
        qos = {"priority": Priority.HIGH, "retries": 5}
        gwy.send_cmd(Command("RQ", dev_id, "0016", "00FF", qos=qos))

    tasks = []

    if kwargs.get(EXECUTE_CMD):  # TODO: wrap in a try?
        spawn_execute_cmd(gwy, **kwargs)

    if kwargs.get(GET_FAULTS):
        tasks += [gwy._loop.create_task(get_faults(gwy, kwargs[GET_FAULTS]))]

    if kwargs.get(GET_SCHED) and kwargs[GET_SCHED][0]:
        tasks += [gwy._loop.create_task(get_schedule(gwy, *kwargs[GET_SCHED]))]

    if kwargs.get(SET_SCHED) and kwargs[SET_SCHED][0]:
        tasks += [gwy._loop.create_task(set_schedule(gwy, *kwargs[SET_SCHED]))]

    if kwargs.get(SCAN_DISC):
        tasks += [gwy._loop.create_task(scan_disc(gwy, d)) for d in kwargs[SCAN_DISC]]

    if kwargs.get(SCAN_FULL):
        tasks += [gwy._loop.create_task(scan_full(gwy, d)) for d in kwargs[SCAN_FULL]]

    if kwargs.get(SCAN_HARD):
        tasks += [gwy._loop.create_task(scan_hard(gwy, d)) for d in kwargs[SCAN_HARD]]

    if kwargs.get(SCAN_XXXX):
        tasks += [gwy._loop.create_task(scan_xxxx(gwy, d)) for d in kwargs[SCAN_XXXX]]

    gwy._tasks.extend(tasks)
    return tasks


async def periodic(gwy, cmd, count=1, interval=None):
    async def _periodic():
        await asyncio.sleep(interval)
        gwy.send_cmd(cmd)

    if interval is None:
        interval = 0 if count == 1 else 60

    if count <= 0:
        while True:
            await _periodic()
    else:
        for _ in range(count):
            await _periodic()


async def schedule_task(delay, func, *args, **kwargs) -> Any:
    """Start a coro after delay seconds."""

    async def scheduled_func(delay, func, *args, **kwargs):
        await asyncio.sleep(delay)
        await func(*args, **kwargs)

    return asyncio.create_task(scheduled_func(delay, func, *args, **kwargs))


async def get_faults(gwy, ctl_id: str):
    ctl_addr = Address(id=ctl_id, type=ctl_id[:2])
    device = gwy._get_device(ctl_addr, ctl_addr=ctl_addr)

    try:
        await device._evo.get_fault_log()  # 0418
    except ExpiredCallbackError as exc:
        _LOGGER.error("get_faults(): Function timed out: %s", exc)


async def get_schedule(gwy, ctl_id: str, zone_idx: str) -> None:
    ctl_addr = Address(id=ctl_id, type=ctl_id[:2])
    zone = gwy._get_device(ctl_addr, ctl_addr=ctl_addr)._evo._get_zone(zone_idx)

    try:
        await zone.get_schedule()
    except ExpiredCallbackError as exc:
        _LOGGER.error("get_schedule(): Function timed out: %s", exc)


async def set_schedule(gwy, ctl_id, schedule) -> None:
    schedule = json.load(schedule)
    zone_idx = schedule["zone_idx"]

    ctl_addr = Address(id=ctl_id, type=ctl_id[:2])
    zone = gwy._get_device(ctl_addr, ctl_addr=ctl_addr)._evo._get_zone(zone_idx)

    try:
        await zone.set_schedule(schedule["schedule"])  # 0404
    except ExpiredCallbackError as exc:
        _LOGGER.error("set_schedule(): Function timed out: %s", exc)


def poll_device(gwy, dev_id) -> List[Any]:
    _LOGGER.warning("poll_device() invoked...")

    qos = {"priority": Priority.LOW, "retries": 0}
    if "poll_codes" in DEVICE_TABLE.get(dev_id[:2]):
        codes = DEVICE_TABLE[dev_id[:2]]["poll_codes"]
    else:
        codes = ["0016", "1FC9"]

    tasks = []

    for code in codes:
        cmd = Command("RQ", dev_id, code, "00", qos=qos)
        tasks.append(gwy._loop.create_task(periodic(gwy, cmd, count=0)))

        cmd = Command("RQ", dev_id, code, "0000", qos=qos)
        tasks.append(gwy._loop.create_task(periodic(gwy, cmd, count=0)))

    gwy._tasks.extend(tasks)
    return tasks


async def scan_disc(gwy, dev_id: str):
    _LOGGER.warning("scan_quick() invoked...")

    device = gwy._get_device(Address(id=dev_id, type=dev_id[:2]))  # not always a CTL
    device._discover()  # discover_flag=DISCOVER_ALL)


async def scan_full(gwy, dev_id: str):
    _LOGGER.warning("scan_full() invoked - expect a lot of Warnings")

    qos = {"priority": Priority.HIGH, "retries": 3}
    gwy.send_cmd(Command._puzzle(message="full scan: begins...", qos=qos))

    qos = {"priority": Priority.DEFAULT, "retries": 5}
    gwy.send_cmd(Command("RQ", dev_id, "0016", "0000", qos=qos))

    qos = {"priority": Priority.DEFAULT, "retries": 1}
    for code in sorted(RAMSES_CODES):
        if code == "0005":
            for zone_type in range(20):  # known up to 18
                gwy.send_cmd(Command("RQ", dev_id, code, f"00{zone_type:02X}", qos=qos))

        elif code == "000C":
            for zone_idx in range(16):  # also: FA-FF?
                gwy.send_cmd(Command("RQ", dev_id, code, f"{zone_idx:02X}00", qos=qos))

        elif code == "0016":
            continue

        elif code == "0404":
            gwy.send_cmd(Command("RQ", dev_id, code, "00200008000100", qos=qos))

        elif code == "0418":
            for log_idx in range(2):
                gwy.send_cmd(Command("RQ", dev_id, code, f"{log_idx:06X}", qos=qos))

        elif code == "1100":
            gwy.send_cmd(Command("RQ", dev_id, code, "FC", qos=qos))

        elif code == "2E04":
            gwy.send_cmd(Command("RQ", dev_id, code, "FF", qos=qos))

        elif code == "3220":
            for data_id in ("00", "03"):  # these are mandatory READ_DATA data_ids
                gwy.send_cmd(Command("RQ", dev_id, code, f"0000{data_id}0000", qos=qos))

        elif code in CODE_SCHEMA and CODE_SCHEMA[code].get("rq_len"):
            rq_len = CODE_SCHEMA[code].get("rq_len") * 2
            gwy.send_cmd(Command("RQ", dev_id, code, f"{0:0{rq_len}X}", qos=qos))

        else:
            gwy.send_cmd(Command("RQ", dev_id, code, "0000", qos=qos))

    # these are possible/difficult codes
    qos = {"priority": Priority.DEFAULT, "retries": 2}
    for code in ("0150", "2389"):
        gwy.send_cmd(Command("RQ", dev_id, code, "0000", qos=qos))

    qos = {"priority": Priority.LOW, "retries": 3}
    gwy.send_cmd(Command._puzzle(message="full scan: ended.", qos=qos))


async def scan_hard(gwy, dev_id: str):
    _LOGGER.warning("scan_deep() invoked - expect some Warnings")

    qos = {"priority": Priority.LOW, "retries": 0}
    for code in range(0x4000):
        gwy.send_cmd(Command("RQ", dev_id, f"{code:04X}", "0000", qos=qos))


async def scan_xxxx(gwy, dev_id: str):
    _LOGGER.warning("scan_xxxx() invoked - expect a lot of nonsense")
    await scan_001(gwy, dev_id)


async def scan_001(gwy, dev_id: str):
    _LOGGER.warning("scan_001() invoked - expect a lot of nonsense")

    qos = {"priority": Priority.LOW, "retries": 3}
    for idx in range(0x10):
        gwy.send_cmd(Command(" W", dev_id, "000E", f"{idx:02X}0050", qos=qos))
        gwy.send_cmd(Command("RQ", dev_id, "000E", f"{idx:02X}00C8", qos=qos))


async def scan_002(gwy, dev_id: str):
    _LOGGER.warning("scan_002() invoked - expect a lot of nonsense")

    # Two modes, I and W & Two headers zz00 and zz
    message = "0000" + "".join([f"{ord(x):02X}" for x in "Hello there."]) + "00"
    qos = {"priority": Priority.LOW, "retries": 0}
    for code in [f"{c:04X}" for c in range(0x4000)]:
        if code in RAMSES_CODES:  # no need to test known codes
            continue
        gwy.send_cmd(Command(" W", dev_id, code, message, qos=qos))


async def scan_003(gwy, dev_id: str):
    _LOGGER.warning("scan_003() invoked - expect a lot of nonsense")

    qos = {"priority": Priority.LOW, "retries": 0}
    for msg_id in range(0x100):
        gwy.send_cmd(Command.get_opentherm_msg(dev_id, msg_id, qos=qos))
