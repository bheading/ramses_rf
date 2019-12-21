"""Evohome serial."""
import asyncio

import argparse
import logging

import ptvsd  # pylint: disable=import-error

from evohome import _CONSOLE, _LOGGER, Gateway

DEBUG_MODE = False
DEBUG_ADDR = "172.27.0.138"
DEBUG_PORT = 5679


_LOGGER.setLevel(logging.DEBUG)
_LOGGER.addHandler(_CONSOLE)


def _parse_args():
    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-p", "--port_name", help="serial port to monitor")
    group.add_argument("-i", "--input_file", help="packet file to parse")

    parser.add_argument("-o", "--output_file", help="log packets to file")
    parser.add_argument("-m", "--message_file", help="log messages to file")

    parser.add_argument("-r", "--raw_packets", action="store_true", help="don't display messages")
    parser.add_argument("-l", "--listen_only", action="store_true", help="only listen")

    parser.add_argument("-x", "--debug_mode", action="store_true", help="debug mode")

    return parser.parse_args()


async def main(loop):
    """Main loop."""
    args = _parse_args()

    if args.debug_mode is True:
        print(f"Debugging is enabled, listening on: {DEBUG_ADDR}:{DEBUG_PORT}.")
        ptvsd.enable_attach(address=(DEBUG_ADDR, DEBUG_PORT))

    if args.debug_mode and DEBUG_MODE is True:
        print("Waiting for debugger to attach...")
        ptvsd.wait_for_attach()
        print("Debugger is attached!")

    gateway = Gateway(
        **vars(args),
        loop=loop
    )

    await gateway.start()


if __name__ == "__main__":  # called from CLI?
    LOOP = asyncio.get_event_loop()
    LOOP.run_until_complete(main(LOOP))
    LOOP.close()
