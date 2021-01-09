#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
"""A CLI for the evohome_rf library.

    evohome_rf is used to parse/process Honeywell's RAMSES-II packets.
    """
import asyncio
from datetime import datetime as dt, timedelta as td
import json
import logging
import shutil
import sys
from threading import Lock
from typing import Tuple

import click
from colorama import init as colorama_init, Fore, Style

from evohome_rf import (  # noqa
    DISABLE_DISCOVERY,
    DISABLE_SENDING,
    ENFORCE_ALLOWLIST,
    EVOFW_FLAG,
    INPUT_FILE,
    PACKET_LOG,
    REDUCE_PROCESSING,
    SERIAL_PORT,
    Gateway,
    GracefulExit,
)
from evohome_rf.command import Command, Priority
from evohome_rf.helpers import dts_to_hex
from evohome_rf.schema import USE_NAMES

count_lock = Lock()
count_rcvd = 0

ALLOW_LIST = "allowlist"
DEBUG_MODE = "debug_mode"
EXECUTE_CMD = "execute_cmd"

CONFIG = "config"
COMMAND = "command"

CONSOLE_COLS = int(shutil.get_terminal_size(fallback=(2e3, 24)).columns - 4)

DONT_CREATE_MESSAGES = 3
DONT_CREATE_ENTITIES = 2
DONT_UPDATE_ENTITIES = 1

DEFAULT_INTERVAL = 5  # should be 180-240

LOWER_FREQ = 0x216200
BASIC_FREQ = 0x21656A
UPPER_FREQ = 0x216800

DEBUG_ADDR = "0.0.0.0"
DEBUG_PORT = 5678

COLORS = {" I": Fore.GREEN, "RP": Fore.CYAN, "RQ": Fore.CYAN, " W": Fore.MAGENTA}

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

LIB_KEYS = (
    INPUT_FILE,
    SERIAL_PORT,
    EVOFW_FLAG,
    PACKET_LOG,
    REDUCE_PROCESSING,
)

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.INFO)


def _proc_kwargs(obj, kwargs) -> Tuple[dict, dict]:
    lib_kwargs, cli_kwargs = obj
    lib_kwargs[CONFIG].update({k: v for k, v in kwargs.items() if k in LIB_KEYS})
    cli_kwargs.update({k: v for k, v in kwargs.items() if k not in LIB_KEYS})
    return lib_kwargs, cli_kwargs


class BasedIntParamType(click.ParamType):
    name = "integer"

    def convert(self, value, param, ctx):
        try:
            if isinstance(value, int):
                return value
            elif value[:2].lower() == "0x":
                return int(value[2:], 16)
            elif value[:1] == "0":
                return int(value, 8)
            elif isinstance(value, str):
                return int(value, 10)
            return int(value)
        except TypeError:
            self.fail(
                "expected string for int() conversion, got "
                f"{value!r} of type {type(value).__name__}",
                param,
                ctx,
            )
        except ValueError:
            self.fail(f"{value!r} is not a valid integer", param, ctx)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("-z", "--debug-mode", count=True, help="enable debugger")
@click.option("-r", "--reduce-processing", count=True, help="-rrr will give packets")
@click.option("-c", "--config-file", type=click.File("r"))
@click.pass_context
def cli(ctx, config_file=None, **kwargs):
    """A CLI for the evohome_rf library."""

    if kwargs[DEBUG_MODE]:
        import debugpy

        debugpy.listen(address=(DEBUG_ADDR, DEBUG_PORT))
        _LOGGER.info(f"Debugging is enabled, listening on: {DEBUG_ADDR}:{DEBUG_PORT}.")
        _LOGGER.info(" - execution paused, waiting for debugger to attach...")
        debugpy.wait_for_client()
        _LOGGER.info(" - debugger is now attached, continuing execution.")

    lib_kwargs, cli_kwargs = _proc_kwargs(({CONFIG: {}}, {}), kwargs)

    if config_file is not None:
        lib_kwargs.update(json.load(config_file))

    lib_kwargs[DEBUG_MODE] = cli_kwargs[DEBUG_MODE] > 1

    red_proc = max((kwargs[REDUCE_PROCESSING], 2))
    lib_kwargs[CONFIG][REDUCE_PROCESSING] = kwargs[REDUCE_PROCESSING] = red_proc
    lib_kwargs[CONFIG][USE_NAMES] = False

    ctx.obj = lib_kwargs, kwargs


class PortCommand(click.Command):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.insert(0, click.Argument(("serial-port",)))
        self.params.insert(
            1,
            click.Option(
                ("-o", "--packet-log"),
                type=click.Path(),
                help="Log all packets to this file",
            ),
        )
        self.params.insert(
            2,
            click.Option(
                ("-T", "--evofw-flag"),
                type=click.STRING,
                help="Pass this traceflag to evofw",
            ),
        )


@click.command(cls=PortCommand)
@click.option(
    "-u",
    "--upper",
    type=BasedIntParamType(),
    default=UPPER_FREQ,
    help="upper frequency (e.g. {LOWER_FREQ}",
)
@click.option(
    "-l",
    "--lower",
    type=BasedIntParamType(),
    default=LOWER_FREQ,
    help=f"lower frequency (e.g. {LOWER_FREQ}",
)
@click.option(
    "-c", "--count", type=int, default=10, help="number of packets to listen for"
)
@click.option(
    "-i",
    "--interval",
    type=float,
    default=DEFAULT_INTERVAL,
    help="expected interval (secs) between packets",
)
@click.pass_obj
def tune(obj, **kwargs):
    """Spawn the puzzle listener."""
    kwargs["interval"] = max((int(kwargs["interval"] * 100) / 100, 0.05))

    lib_kwargs, cli_kwargs = _proc_kwargs(obj, kwargs)
    lib_kwargs[CONFIG][DISABLE_SENDING] = True  # bypassed by calling _write_data

    asyncio.run(main(lib_kwargs, command="tune", **cli_kwargs))


@click.command(cls=PortCommand)
@click.option(
    "-c",
    "--count",
    type=int,
    default=0,
    help="number of packets to send (0 is unlimited)",
)
@click.option(
    "-i",
    "--interval",
    type=float,
    default=DEFAULT_INTERVAL,
    help="minimum interval (secs) between packets",
)
# @click.option(
#     "-l",
#     "--packet_length",
#     type=int,
#     default=48,
#     help="length of puzzle packet",
# )
@click.pass_obj
def cast(obj, **kwargs):  # HACK: remove?
    """Spawn the puzzle caster."""
    kwargs["interval"] = max((int(kwargs["interval"] * 100) / 100, 0.05))

    lib_kwargs, cli_kwargs = _proc_kwargs(obj, kwargs)
    lib_kwargs[CONFIG][DISABLE_DISCOVERY] = True

    lib_kwargs[ALLOW_LIST] = {"18:000730": {}}  # TODO: messy
    lib_kwargs[CONFIG][ENFORCE_ALLOWLIST] = False

    asyncio.run(main(lib_kwargs, command="cast", **cli_kwargs))


async def puzzle_cast(gwy, pkt_protocol, interval=None, count=0, length=48, **kwargs):
    def print_message(msg) -> None:
        dtm = f"{msg.dtm:%H:%M:%S.%f}"[:-3]
        if msg.code == "7FFF":
            _LOGGER.info(f"{Style.BRIGHT}{Fore.CYAN}{dtm} {msg}"[:CONSOLE_COLS])
        else:
            _LOGGER.info(f"{Fore.GREEN}{dtm} {msg}"[:CONSOLE_COLS])

    async def _periodic(ordinal):
        payload = f"7F{dts_to_hex(dt.now())}7F{ordinal % 0x10000:04X}7F{int_hex}7F"
        payload = payload.ljust(length * 2, "F")

        qos = {"priority": Priority.ASAP, "retries": 0}
        await msg_protocol.send_data(
            Command(" I", "63:262142", "7FFF", payload, qos=qos)
        )

    msg_protocol, _ = gwy.create_client(print_message)

    int_hex = f"{int(interval * 100):04X}"

    if count <= 0:
        counter = 0
        while True:
            asyncio.create_task(_periodic(counter))
            await asyncio.sleep(interval)
            counter += 1
    else:
        for counter in range(count):
            asyncio.create_task(_periodic(counter))
            await asyncio.sleep(interval)


async def puzzle_tune(
    gwy,
    pkt_protocol,
    lower=LOWER_FREQ,
    upper=UPPER_FREQ,
    interval=None,
    count=3,
    **kwargs,
):
    def print_message(msg) -> None:
        dtm = f"{msg.dtm:%H:%M:%S.%f}"[:-3]
        if msg.code == "7FFF":
            _LOGGER.info(f"{Style.BRIGHT}{Fore.CYAN}{dtm} {msg}"[:CONSOLE_COLS])
        else:
            _LOGGER.info(f"{Fore.GREEN}{dtm} {msg}"[:CONSOLE_COLS])

    async def set_freq(frequency):
        # data = "!V\r\n"
        # await pkt_protocol._write_data(bytes(data.encode("ascii")))

        hex = f"{frequency:06X}"
        data = f"!C 0D {hex[:2]} {hex[2:4]} {hex[4:]}\r\n"
        await pkt_protocol._write_data(bytes(data.encode("ascii")))

    def process_message(msg) -> None:
        global count_rcvd

        count_lock.acquire()
        count_rcvd += 1
        count_lock.release()

        # if msg.payload["interval"] != interval:
        #     raise RuntimeError("Intervals don't match")

    async def check_reception(freq, count) -> bool:
        global count_rcvd
        count_lock.acquire()
        count_rcvd = 0
        count_lock.release()

        _LOGGER.info(f"  Checking 0x{freq:06X} for max. {interval * count}s")
        dtm_start = dt.now()
        dtm_end = dtm_start + td(seconds=interval * count)
        while dt.now() < dtm_end:
            await asyncio.sleep(0.005)
            if count_rcvd > 0:
                break

        _LOGGER.info("    result = " + ("some" if count_rcvd else "NO") + "thing heard")
        return count_rcvd

        # i = int(((dt.now() - dtm_start).total_seconds() + 0) / interval)
        # result = count_rcvd / i if i != 0 else 1
        # _LOGGER.info(f" - result = {result} ({count_rcvd}/{i} pkts/intervals)")
        # return result

    async def binary_chop(x, y, threshold=0) -> Tuple[int, float]:  # 1, 2
        _LOGGER.info(f"Puzzling from 0x{x:06X} to 0x{y:06X}...")

        # while x != y:
        #     freq = int((x + y) / 2)
        #     await set_freq(freq)
        #     result = await check_reception(freq, count)
        #     if result > threshold:
        #         if lower:
        #             y = freq
        #         else:
        #             x = freq
        #     else:
        #         if lower:
        #             x = freq
        #         else:
        #             y = freq

        # return x, result

        direction = 1 if x < y else -1  # 1 is ascending, initially
        freq = int((x + y) / 2)
        while True:
            await set_freq(freq)
            result = await check_reception(freq, count=1)

            if freq in (x, y):
                return freq, result

            print()

            if result > threshold:
                new_freq = int((freq + x - direction) / 2)  # go back towards x
            else:
                new_freq = int((freq + y + direction) / 2)  # continue away from x

            freq = new_freq

    gwy.create_client(print_message)
    gwy.create_client(process_message)

    dtm_expires = dt.now() + td(seconds=3)
    while dt.now() < dtm_expires:
        await asyncio.sleep(0.1)
        if pkt_protocol._has_initialized:
            break
    # else:
    #     raise RuntimeError("Can't find serial interface")

    print("")
    _LOGGER.info("STEP 0: No changes to freq")
    result = await check_reception(0, count=3)
    _LOGGER.info(f"STEP 0: Result = 0x{0:06X} ({result:.2f}) (no changes to freq)")

    print("")
    _LOGGER.info("STEP 1: Freq changed to default, 0x{BASIC_FREQ:06X}")
    await set_freq(BASIC_FREQ)
    result = await check_reception(BASIC_FREQ, count=3)
    _LOGGER.info(f"STEP 1: Result = 0x{BASIC_FREQ:06X} ({result:.2f}) (freq set to default)")

    print("")
    _LOGGER.info("STEP 2: Calibrate up from 0x{lower:06X} to 0x{BASIC_FREQ:06X}")
    lower_freq, result1 = await binary_chop(lower, BASIC_FREQ)
    _LOGGER.info(f"STEP 2: Result = 0x{lower_freq:06X} ({result1:.2f}) (upwards calibrated)")

    print("")
    _LOGGER.info("STEP 3: Calibrate down from 0x{upper:06X} to 0x{lower_freq:06X}")
    upper_freq, result2 = await binary_chop(upper, lower_freq)
    _LOGGER.info(f"STEP 3: Result = 0x{lower_freq:06X} ({result1:.2f}) (downwards calibrated)")

    print("")
    _LOGGER.info(
        f"OVERALL Result = 0x{int((lower_freq + upper_freq) / 2):06X} "
        f"(0x{lower_freq:06X}-0x{upper_freq:06X}, {result1:.2f}, {result2:.2f})"
    )


async def main(lib_kwargs, **kwargs):

    print("\r\nclient.py: Starting evohome_rf (puzzler)...")

    if sys.platform == "win32":  # is better than os.name
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if kwargs[REDUCE_PROCESSING] < DONT_CREATE_MESSAGES:
        colorama_init(autoreset=True)

    gwy = Gateway(lib_kwargs[CONFIG].pop(SERIAL_PORT, None), **lib_kwargs)

    asyncio.create_task(gwy.start())
    while gwy.pkt_protocol is None:
        await asyncio.sleep(0.05)
    pkt_protocol = gwy.pkt_protocol

    if kwargs[COMMAND] == "cast":
        task = asyncio.create_task(puzzle_cast(gwy, pkt_protocol, **kwargs))
    else:  # kwargs[COMMAND] == "tune":
        task = asyncio.create_task(puzzle_tune(gwy, pkt_protocol, **kwargs))

    try:  # main code here
        await task

    except asyncio.CancelledError:
        # _LOGGER.info(" - exiting via: CancelledError (this is expected)")
        pass
    except GracefulExit:
        _LOGGER.info(" - exiting via: GracefulExit")
    except KeyboardInterrupt:
        _LOGGER.info(" - exiting via: KeyboardInterrupt")
    else:  # if no Exceptions raised, e.g. EOF when parsing
        # _LOGGER.info(" - exiting via: else-block (e.g. EOF when parsing)")
        pass

    print("\r\nclient.py: Finished evohome_rf.\r\n")


cli.add_command(tune)
cli.add_command(cast)

if __name__ == "__main__":
    cli()
