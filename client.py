#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
"""A CLI for the ramses_rf library.

ramses_rf is used to parse/process Honeywell's RAMSES-II packets.
"""

# import cProfile
# import pstats

import asyncio
import json
import logging
import sys
from typing import Tuple

import click
from colorama import Fore, Style
from colorama import init as colorama_init

from ramses_rf import Gateway, GracefulExit
from ramses_rf.address import is_valid_dev_id
from ramses_rf.command import Command
from ramses_rf.discovery import (
    EXECUTE_CMD,
    GET_FAULTS,
    GET_SCHED,
    SCAN_DISC,
    SCAN_FULL,
    SCAN_HARD,
    SCAN_XXXX,
    SET_SCHED,
    spawn_execute_scripts,
    spawn_monitor_scripts,
)
from ramses_rf.exceptions import EvohomeError
from ramses_rf.logger import CONSOLE_COLS, DEFAULT_DATEFMT, DEFAULT_FMT, LOG_FILE_NAME
from ramses_rf.schema import (
    ALLOW_LIST,
    CONFIG,
    DISABLE_DISCOVERY,
    DISABLE_SENDING,
    DONT_CREATE_MESSAGES,
    ENFORCE_ALLOWLIST,
    EVOFW_FLAG,
    INPUT_FILE,
    PACKET_LOG,
    PACKET_LOG_SCHEMA,
    REDUCE_PROCESSING,
    SERIAL_PORT,
)

DEBUG_MODE = "debug_mode"

# this is called after import colorlog to ensure its handlers wrap the correct streams
logging.basicConfig(level=logging.WARNING, format=DEFAULT_FMT, datefmt=DEFAULT_DATEFMT)


COMMAND = "command"
EXECUTE = "execute"
LISTEN = "listen"
MONITOR = "monitor"
PARSE = "parse"

DEBUG_ADDR = "0.0.0.0"
DEBUG_PORT = 5678

COLORS = {" I": Fore.GREEN, "RP": Fore.CYAN, "RQ": Fore.CYAN, " W": Fore.MAGENTA}

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

LIB_KEYS = (
    INPUT_FILE,
    SERIAL_PORT,
    EVOFW_FLAG,
    PACKET_LOG,
    # "process_level",  # TODO
    REDUCE_PROCESSING,
)


def normalise_config_schema(config) -> Tuple[str, dict]:
    """Convert a HA config dict into the client library's own format."""

    serial_port = config[CONFIG].pop(SERIAL_PORT, None)

    if config[CONFIG].get(PACKET_LOG):
        if not isinstance(config[CONFIG][PACKET_LOG], dict):
            config[CONFIG][PACKET_LOG] = PACKET_LOG_SCHEMA(
                {LOG_FILE_NAME: config[CONFIG][PACKET_LOG]}
            )
    else:
        config[CONFIG][PACKET_LOG] = {}

    return serial_port, config


def _proc_kwargs(obj, kwargs) -> Tuple[dict, dict]:
    lib_kwargs, cli_kwargs = obj
    lib_kwargs[CONFIG].update({k: v for k, v in kwargs.items() if k in LIB_KEYS})
    cli_kwargs.update({k: v for k, v in kwargs.items() if k not in LIB_KEYS})
    return lib_kwargs, cli_kwargs


def _convert_to_list(d: str) -> list:
    if not d or not str(d):
        return []
    return [c.strip() for c in d.split(",") if c.strip()]


def _arg_split(ctx, param, value):  # callback=_arg_split
    return [x.strip() for x in value.split(",")]


class DeviceIdParamType(click.ParamType):
    name = "device_id"

    def convert(self, value: str, param, ctx):
        if is_valid_dev_id(value):
            return value.upper()
        self.fail(f"{value!r} is not a valid device_id", param, ctx)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("-z", "--debug-mode", count=True, help="enable debugger")
@click.option("-r", "--reduce-processing", count=True, help="-rrr will give packets")
@click.option("-l/-nl", "--long-dates/--no-long-dates", default=None)
@click.option("-c", "--config-file", type=click.File("r"))
@click.option("-k", "--client-state", type=click.File("r"))
@click.option("-sd", "--show-device", help="show these devices")
@click.option("-sc", "--show-schema", is_flag=True, help="show the system schema")
@click.option("-sp", "--show-params", is_flag=True, help="show the system params")
@click.option("-ss", "--show-status", is_flag=True, help="show the system status")
@click.option("-st", "--show-state", is_flag=True, help="dump the state database")
@click.pass_context
def cli(ctx, config_file=None, **kwargs):
    """A CLI for the ramses_rf library."""

    if 0 < kwargs[DEBUG_MODE] < 3:
        import debugpy

        debugpy.listen(address=(DEBUG_ADDR, DEBUG_PORT))
        print(f"Debugging is enabled, listening on: {DEBUG_ADDR}:{DEBUG_PORT}.")
        print(" - execution paused, waiting for debugger to attach...")

        if kwargs[DEBUG_MODE] == 1:
            debugpy.wait_for_client()
            print(" - debugger is now attached, continuing execution.")

    lib_kwargs, cli_kwargs = _proc_kwargs(({CONFIG: {}}, {}), kwargs)

    if config_file:
        lib_kwargs.update(json.load(config_file))

    lib_kwargs[DEBUG_MODE] = cli_kwargs[DEBUG_MODE] > 1
    lib_kwargs[CONFIG][REDUCE_PROCESSING] = kwargs[REDUCE_PROCESSING]

    ctx.obj = lib_kwargs, kwargs


class FileCommand(click.Command):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.insert(
            0, click.Argument(("input-file",), type=click.File("r"), default=sys.stdin)
        )
        # NOTE: The following is useful for only for test/dev
        # self.params.insert(
        #     1,
        #     click.Option(
        #         ("-o", "--packet-log"),
        #         type=click.Path(),
        #         help="Log all packets to this file",
        #     ),
        # )


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


@click.command(cls=FileCommand)
@click.pass_obj
def parse(obj, **kwargs):
    """Parse a log file for messages/packets."""

    lib_kwargs, cli_kwargs = _proc_kwargs(obj, kwargs)

    lib_kwargs[INPUT_FILE] = lib_kwargs[CONFIG].pop(INPUT_FILE)

    asyncio.run(main(lib_kwargs, command=PARSE, **cli_kwargs))


@click.command(cls=PortCommand)
@click.option("-d/-nd", "--discover/--no-discover", default=None)
@click.option(  # "--execute-cmd"
    "-x", "--execute-cmd", type=click.STRING, help="e.g. 'RQ 01:123456 1F09 00'"
)
@click.option(
    "--poll-devices", type=click.STRING, help="e.g. 'device_id, device_id, ...'"
)
@click.pass_obj
def monitor(obj, **kwargs):
    """Monitor (eavesdrop and/or probe) a serial port for messages/packets."""
    lib_kwargs, cli_kwargs = _proc_kwargs(obj, kwargs)

    if cli_kwargs["discover"] is not None:
        lib_kwargs[CONFIG][DISABLE_DISCOVERY] = not cli_kwargs["discover"]
    lib_kwargs[CONFIG]["poll_devices"] = _convert_to_list(
        cli_kwargs.pop("poll_devices")
    )

    asyncio.run(main(lib_kwargs, command=MONITOR, **cli_kwargs))


@click.command(cls=PortCommand)
@click.option(  # "--execute-cmd"
    "-x", "--execute-cmd", type=click.STRING, help="e.g. 'RQ 01:123456 1F09 00'"
)
@click.option("-S0", "-SD", "--scan-disc", help="e.g. 'device_id, device_id, ...'")
@click.option("-S1", "-SF", "--scan-full", help="e.g. 'device_id, device_id, ...'")
@click.option("-S2", "-SH", "--scan-hard", help="e.g. 'device_id, device_id, ...'")
@click.option("-S9", "-SX", "--scan-xxxx", help="e.g. 'device_id, device_id, ...'")
@click.option("--get-faults", type=DeviceIdParamType(), help="controller_id")
@click.option(  # "--get-schedule"
    "--get-schedule",
    default=[None, None],
    type=(DeviceIdParamType(), str),
    help="controller_id, zone_idx (e.g. '0A')",
)
@click.option(  # "--set-schedule"
    "--set-schedule",
    default=[None, None],
    type=(DeviceIdParamType(), click.File("r")),
    help="controller_id, filename.json",
)
@click.pass_obj
def execute(obj, **kwargs):
    """Execute any specified scripts, return the results, then quit."""
    lib_kwargs, cli_kwargs = _proc_kwargs(obj, kwargs)

    lib_kwargs[CONFIG][DISABLE_DISCOVERY] = True

    allowed = lib_kwargs[ALLOW_LIST] = lib_kwargs.get(ALLOW_LIST, {})
    for k in (SCAN_DISC, SCAN_FULL, SCAN_HARD, SCAN_XXXX):
        cli_kwargs[k] = _convert_to_list(cli_kwargs.pop(k))
        allowed.update({d: None for d in cli_kwargs[k] if d not in allowed})

    if cli_kwargs.get(GET_FAULTS) and cli_kwargs[GET_FAULTS] not in allowed:
        allowed[cli_kwargs[GET_FAULTS]] = None

    if cli_kwargs[GET_SCHED][0] and cli_kwargs[GET_SCHED][0] not in allowed:
        allowed[cli_kwargs[GET_SCHED][0]] = None

    if cli_kwargs[SET_SCHED][0] and cli_kwargs[SET_SCHED][0] not in allowed:
        allowed[cli_kwargs[SET_SCHED][0]] = None

    if lib_kwargs[ALLOW_LIST]:
        lib_kwargs[CONFIG][ENFORCE_ALLOWLIST] = True

    asyncio.run(main(lib_kwargs, command=EXECUTE, **cli_kwargs))


@click.command(cls=PortCommand)
@click.pass_obj
def listen(obj, **kwargs):
    """Listen to (eavesdrop only) a serial port for messages/packets."""
    lib_kwargs, cli_kwargs = _proc_kwargs(obj, kwargs)

    lib_kwargs[CONFIG][DISABLE_SENDING] = True

    asyncio.run(main(lib_kwargs, command=LISTEN, **cli_kwargs))


def _print_results(gwy, **kwargs):

    if kwargs[GET_FAULTS]:
        fault_log = gwy.system_by_id[kwargs[GET_FAULTS]]._fault_log.fault_log

        if fault_log is None:
            print("No fault log, or failed to get the fault log.")
        else:
            [print(f"{k:02X}", v) for k, v in fault_log.items()]

    if kwargs[GET_SCHED][0]:
        system_id, zone_idx = kwargs[GET_SCHED]
        zone = gwy.system_by_id[system_id].zone_by_idx[zone_idx]
        schedule = zone._schedule.schedule

        if schedule is None:
            print("Failed to get the schedule.")
        else:
            print("Schedule = \r\n", json.dumps(schedule))  # , indent=4))

    if kwargs[SET_SCHED][0]:
        system_id, _ = kwargs[GET_SCHED]

    # else:
    #     print(gwy.device_by_id[kwargs["device_id"]])


def _save_state(gwy):
    schema, msgs = gwy._get_state()

    with open("state_msgs.log", "w") as f:
        [
            f.write(f"{m.dtm.isoformat(sep='T')} {m._pkt}\r\n")
            for m in msgs.values()
            # if not m._expired
        ]

    with open("state_schema.json", "w") as f:
        f.write(json.dumps(schema, indent=4))

    # await gwy._set_state(schema, msgs)


def _print_state(gwy, **kwargs):
    (schema, packets) = gwy._get_state(include_expired=True)

    print(f"Schema  = {json.dumps(schema, indent=4)}\r\n")
    # print(f"Packets = {json.dumps(packets, indent=4)}\r\n")
    [print(f"{dtm} {pkt}") for dtm, pkt in packets.items()]


def _print_summary(gwy, **kwargs):
    if gwy.evo is None:
        print(f"Schema[gateway] = {json.dumps(gwy.schema, indent=4)}\r\n")
        print(f"Params[gateway] = {json.dumps(gwy.params)}\r\n")
        print(f"Status[gateway] = {json.dumps(gwy.status)}")
        return

    print(f"Schema[{repr(gwy.evo)}] = {json.dumps(gwy.evo.schema, indent=4)}\r\n")
    print(f"Params[{repr(gwy.evo)}] = {json.dumps(gwy.evo.params, indent=4)}\r\n")
    print(f"Status[{repr(gwy.evo)}] = {json.dumps(gwy.evo.status, indent=4)}\r\n")

    orphans = [d for d in sorted(gwy.devices) if d not in gwy.evo.devices]
    devices = {d.id: d.schema for d in orphans}
    print(f"Schema[orphans] = {json.dumps({'schema': devices}, indent=4)}\r\n")
    devices = {d.id: d.params for d in orphans}
    print(f"Params[orphans] = {json.dumps({'params': devices}, indent=4)}\r\n")
    devices = {d.id: d.status for d in orphans}
    print(f"Status[orphans] = {json.dumps({'status': devices}, indent=4)}\r\n")


async def main(lib_kwargs, **kwargs):
    def process_message(msg) -> None:
        # if msg._pkt._idx not in (None, "******"):
        #     return
        dtm = msg.dtm if kwargs["long_dates"] else f"{msg.dtm:%H:%M:%S.%f}"[:-3]
        if msg.src.type == "18":
            print(f"{Style.BRIGHT}{COLORS.get(msg.verb)}{dtm} {msg}"[:CONSOLE_COLS])
        else:
            print(f"{COLORS.get(msg.verb)}{dtm} {msg}"[:CONSOLE_COLS])

    print("\r\nclient.py: Starting ramses_rf...")

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    serial_port, lib_kwargs = normalise_config_schema(lib_kwargs)
    gwy = Gateway(serial_port, **lib_kwargs)

    if kwargs[REDUCE_PROCESSING] < DONT_CREATE_MESSAGES:
        # no MSGs will be sent to STDOUT, so send PKTs instead
        colorama_init(autoreset=True)  # TODO: remove strip=True
        protocol, _ = gwy.create_client(process_message)

    try:  # main code here
        if not kwargs["client_state"]:
            task = asyncio.create_task(gwy.start())

        if kwargs["client_state"]:
            print("Restoring client state...")
            state = json.load(kwargs["client_state"])
            await gwy._set_state(**state["data"]["client_state"])

        elif kwargs[COMMAND] == EXECUTE:
            tasks = spawn_execute_scripts(gwy, **kwargs)
            await asyncio.gather(*tasks)

            cmds = (EXECUTE_CMD, SCAN_DISC, SCAN_FULL, SCAN_HARD, SCAN_XXXX)
            if not any(kwargs[k] for k in cmds):
                # await gwy.stop()
                task.cancel()

        elif kwargs[COMMAND] == MONITOR:
            tasks = spawn_monitor_scripts(gwy, **kwargs)

        if False:  # TODO: temp test code

            def callback(msg):
                print(msg or "Callback has expired")

            await asyncio.sleep(3)  # allow to quiesce
            cmd = Command.get_zone_name("01:145039", "00")

            if True:
                gwy.send_cmd(cmd, callback=callback)
            else:
                try:
                    print(await gwy.async_send_cmd(cmd, awaitable=False))
                except TimeoutError:
                    print("TimeoutError")

        if not kwargs["client_state"]:
            await task

    except asyncio.CancelledError:
        msg = " - ended via: CancelledError (e.g. SIGINT)"
    except GracefulExit:
        msg = " - ended via: GracefulExit"
    except KeyboardInterrupt:
        msg = " - ended via: KeyboardInterrupt"
    except EvohomeError as err:
        msg = f" - ended via: EvohomeError: {err}"
    else:  # if no Exceptions raised, e.g. EOF when parsing
        msg = " - ended without error (e.g. EOF)"

    print("\r\nclient.py: Finished ramses_rf, results:\r\n")

    if kwargs["show_schema"] or kwargs["show_params"] or kwargs["show_status"]:
        _print_summary(gwy, **kwargs)

    if kwargs["show_state"]:
        _print_state(gwy, **kwargs)

    elif not any(
        (
            kwargs["show_schema"],
            kwargs["show_params"],
            kwargs["show_status"],
            kwargs[COMMAND] == EXECUTE,
            kwargs["show_device"],
            kwargs["show_state"],
        )
    ):
        _print_summary(gwy)

    # if kwargs["show_device"]:
    #     _print_state(gwy)

    if kwargs[COMMAND] == EXECUTE:
        _print_results(gwy, **kwargs)

    print(f"\r\nclient.py: Finished ramses_rf.\r\n{msg}\r\n")


cli.add_command(parse)
cli.add_command(monitor)
cli.add_command(execute)
cli.add_command(listen)

if __name__ == "__main__":
    # profile = cProfile.Profile()

    try:
        # profile.run("cli()")
        cli()
    except SystemExit:
        pass

    # ps = pstats.Stats(profile)
    # ps.sort_stats(pstats.SortKey.TIME).print_stats(60)
