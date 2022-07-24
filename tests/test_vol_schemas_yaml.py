#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
"""RAMSES RF - a RAMSES-II protocol decoder & analyser.

Test the configuration parsers.
"""

import pytest
import voluptuous as vol
import yaml

from ramses_rf.protocol.schemas import (
    SCH_GLOBAL_TRAITS,
    SCH_PACKET_LOG,
    SCH_SERIAL_PORT,
)
from ramses_rf.schemas import SCH_GLOBAL_SCHEMAS, SCH_RESTORE_CACHE


def _test_schema(validator: vol.Schema, schema: str) -> dict:
    return validator(yaml.safe_load(schema))


def _test_schema_bad(validator: vol.Schema, schema: str) -> None:
    try:
        _test_schema(validator, schema)
    except (vol.MultipleInvalid, yaml.YAMLError):
        pass
    else:
        raise TypeError  # should be invalid YAML, but isn't


KNOWN_LIST_BAD = (
    """
    #  expected a dictionary
    """,
    """
    other_key: null  # extra keys not allowed @ data['other_key']
    """,
    """
    known_list: []  # expected a dictionary for dictionary value @ data['known_list']
    """,
)
KNOWN_LIST_GOOD = (
    """
    {}
    """,
    """
    known_list: {}
    """,
    """
    known_list: {}
    block_list: {}
    """,
)


@pytest.mark.parametrize("index", range(len(KNOWN_LIST_BAD)))
def test_known_list_bad(index, schemas=KNOWN_LIST_BAD):
    _test_schema_bad(SCH_GLOBAL_TRAITS, schemas[index])


@pytest.mark.parametrize("index", range(len(KNOWN_LIST_GOOD)))
def test_known_list_good(index, schemas=KNOWN_LIST_GOOD):
    _test_schema(SCH_GLOBAL_TRAITS, schemas[index])


PACKET_LOG_BAD = (
    """
    #  expected a dictionary
    """,
    """
    {}  # required key not provided @ data['packet_log']
    """,
    """
    other_key: null  # extra keys not allowed @ data['other_key']
    """,
    """
    packet_log: null  # expected str for dictionary value @ data['packet_log']
    """,
    """
    packet_log:
      file_name: null  # expected str for dictionary value @ data['packet_log']['file_name']
    """,
    """
    packet_log:  # required key not provided @ data['packet_log']['file_name']
      rotate_backups: 7
      rotate_bytes: 204800
    """,
)
PACKET_LOG_GOOD = (
    """
    packet_log: packet.log
    """,
    """
    packet_log:
      file_name: packet.log
    """,
    """
    packet_log:
      file_name: packet.log
      rotate_backups: 7
    """,
    """
    packet_log:
      file_name: packet.log
      rotate_bytes: 204800
    """,
    """
    packet_log:
      file_name: packet.log
      rotate_backups: 7
      rotate_bytes: 204800
    """,
)


@pytest.mark.parametrize("index", range(len(PACKET_LOG_BAD)))
def test_packet_log_bad(index, schemas=PACKET_LOG_BAD):
    _test_schema_bad(SCH_PACKET_LOG, schemas[index])


@pytest.mark.parametrize("index", range(len(PACKET_LOG_GOOD)))
def test_packet_log_good(index, schemas=PACKET_LOG_GOOD):
    _test_schema(SCH_PACKET_LOG, schemas[index])


RESTORE_CACHE_BAD = (
    """
    #  expected a dictionary
    """,
    """
    other_key: null  # extra keys not allowed @ data['other_key']
    """,
    """
    restore_cache: none  # should be boolean
    """,
    """
    restore_cache: true
      restore_schema: true  # yaml.scanner.ScannerError
    """,
    """
    restore_schema: true  # should be: restore_cache: restore_schema: true
    """,
    """
    restore_state: false  # should be: restore_cache: restore_state: true
    """,
)
RESTORE_CACHE_GOOD = (
    """
    {}
    """,
    """
    restore_cache: false
    """,
    """
    restore_cache: true
    """,
    """
    restore_cache:
      restore_schema: true
    """,
    """
    restore_cache:
      restore_state:  true
    """,
    """
    restore_cache:
      restore_schema: true
      restore_state:  false
    """,
    """
    restore_cache:
      restore_schema: false
      restore_state:  true
    """,
)


@pytest.mark.parametrize("index", range(len(RESTORE_CACHE_BAD)))
def test_restore_cache_bad(index, schemas=RESTORE_CACHE_BAD):
    _test_schema_bad(SCH_RESTORE_CACHE, schemas[index])


@pytest.mark.parametrize("index", range(len(RESTORE_CACHE_GOOD)))
def test_restore_cache_good(index, schemas=RESTORE_CACHE_GOOD):
    _test_schema(SCH_RESTORE_CACHE, schemas[index])


SCHEMAS_TCS_BAD = (
    """
    #  expected a dictionary
    """,
    """
    other_key: null  # extra keys not allowed @ data['other_key']
    """,
    """
    01:111111:  # expected a dictionary for dictionary value @ data['01:111111']
    """,
    """
    01:111111:
      system:  # expected a dictionary for dictionary value @ data['01:111111']['system']
    """,
    """
    13:111111:  # should be: 01:111111
      system: {}  # The ventilation control system schema must include at least one of [remotes, sensors] @ data['13:111111']
    """,
    """
    01:111111:
      system:
        appliance_control: 10:111111
      zones:  # should be "00"
        00: {}  # extra keys not allowed @ data['01:111111']['zones'][0]
    """,
    """
    01:111111:
      system:
        appliance_control: 10:111111
      zones:
        "00":  # extra keys not allowed @ data['01:111111']['zones']['00']
    """,
    """
    01:111111:
      system:
        appliance_control: 10:111111
      zones:
        "1C": {}  # extra keys not allowed @ data['01:111111']['zones']['1C']
    """,
)
SCHEMAS_TCS_GOOD = (
    """
    {}
    """,
    """
    01:111111: {}
    """,
    """
    01:111111: {is_tcs: true}
    """,
    """
    01:111111:
      system: {}
    """,
    """
    01:111111:
      system:
        appliance_control:
    """,
    """
    01:111111:
      system:
        appliance_control: null
    """,
    """
    01:111111:
      system:
        appliance_control: 10:111111
    """,
    """
    01:111111:
      system:
        appliance_control: 10:111111
    01:222222:
      system:
        appliance_control: 13:222222
    """,
    """
    01:111111:
      system:
        appliance_control: 10:111111
      zones:
        "0B": {}
    """,
    """
    01:111111:
      system:
        appliance_control: 10:111111
      zones:
        "00": {}
        "01": {}
        "02": {}
        "03": {}
    """,
)


@pytest.mark.parametrize("index", range(len(SCHEMAS_TCS_BAD)))
def test_schemas_tcs_bad(index, schemas=SCHEMAS_TCS_BAD):
    _test_schema_bad(SCH_GLOBAL_SCHEMAS, schemas[index])


@pytest.mark.parametrize("index", range(len(SCHEMAS_TCS_GOOD)))
def test_schemas_tcs_good(index, schemas=SCHEMAS_TCS_GOOD):
    _test_schema(SCH_GLOBAL_SCHEMAS, schemas[index])


SCHEMAS_VCS_BAD = (
    """
    #  expected a dictionary
    """,
    """
    other_key: null  # extra keys not allowed @ data['other_key']
    """,
    """
    32:111111:  # expected a dictionary for dictionary value @ data['01:111111']
    """,
    """
    32:111111: {is_vcs: true}
    """,
    """
    01:111111:
      remotes: []
      is_tcs: true
    """,
    """
    32:111111:  # should not duplicate device_id
      remotes: [29:111111, 29:111111]  # not a valid value for dictionary value @ data['32:111111']['remotes']
    """,
    """
    32:111111: {remotes: [29:111111, 29:222222]}
    32:222222: {remotes: [29:111111, 29:222222]}
    32:111112: {remotes: [29:111111, 29:222222]}
    """,
)
SCHEMAS_VCS_GOOD = (
    """
    {}
    """,
    """
    01:111111:
      remotes: []
    """,
    """
    32:111111:
      remotes: []
    """,
    """
    32:111111:
      remotes: []
      is_vcs: true
    """,
    """
    32:111111:
      remotes: [29:111111]
    """,
    """
    32:111111:
      remotes: [29:111111, 29:222222]
      sensors: [29:111111, 29:333333]
      is_vcs: true
    """,
    """
    32:111111: {remotes: [29:111111, 29:222222]}
    32:222222: {remotes: [29:111111, 29:222222]}
    32:333333: {remotes: [29:111111, 29:222222]}
    """,
)


@pytest.mark.parametrize("index", range(len(SCHEMAS_VCS_BAD)))
def test_schemas_vcs_bad(index, schemas=SCHEMAS_VCS_BAD):
    _test_schema_bad(SCH_GLOBAL_SCHEMAS, schemas[index])


@pytest.mark.parametrize("index", range(len(SCHEMAS_VCS_GOOD)))
def test_schemas_vcs_good(index, schemas=SCHEMAS_VCS_GOOD):
    _test_schema(SCH_GLOBAL_SCHEMAS, schemas[index])


SERIAL_PORT_BAD = (
    """
    #  expected a dictionary
    """,
    """
    {}  # required key not provided @ data['serial_port']
    """,
    """
    other_key: null  # extra keys not allowed @ data['other_key']
    """,
    """
    serial_name: /dev/ttyMOCK  # should be: serial_port:
    """,
    """
    serial_port: /dev/ttyMOCK  # yaml.parser.ParserError
      baudrate: 115200  # default
    """,
    """
    serial_port:
      port_name: /dev/ttyMOCK
      baud_rate: 57600  # should be: baudrate:
    """,
    """
    serial_port:
      port_name: /dev/ttyMOCK
        baudrate: 57600  # yaml.parser.ScannerError
    """,
)
SERIAL_PORT_GOOD = (
    """
    serial_port: /dev/ttyMOCK
    """,
    """
    serial_port:
      port_name: /dev/ttyMOCK
    """,
    """
    serial_port:
      port_name: /dev/ttyMOCK
      baudrate: 115200  # default
    """,
    """
    serial_port:
      port_name: /dev/ttyMOCK
      baudrate: 57600
    """,
    """
    serial_port:
      port_name: /dev/ttyMOCK
      baudrate: 57600
    """,
    """
    serial_port:
      port_name: /dev/ttyMOCK
      baudrate: 57600
      dsrdtr: false
      rtscts: false
      timeout: 0
      xonxoff: true
    """,
)


@pytest.mark.parametrize("index", range(len(SERIAL_PORT_BAD)))
def test_serial_port_bad(index, schemas=SERIAL_PORT_BAD):
    _test_schema_bad(SCH_SERIAL_PORT, schemas[index])


@pytest.mark.parametrize("index", range(len(SERIAL_PORT_GOOD)))
def test_serial_port_good(index, schemas=SERIAL_PORT_GOOD):
    _test_schema(SCH_SERIAL_PORT, schemas[index])
