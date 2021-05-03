# ramses_rf

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black) [![CircleCI](https://circleci.com/gh/zxdavb/ramses_rf.svg?style=svg)](https://circleci.com/gh/zxdavb/ramses_rf) [![Join the chat at https://gitter.im/ramses_rf/community](https://badges.gitter.im/ramses_rf/community.svg)](https://gitter.im/ramses_rf/community?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)

It does three things:
a) convert the RF packets in useful JSON
b) builds a picture (schema, config & state) of an evohome-compatible system - either passively (by eavesdropping), or actively (probing)
c) allows you to send commands to evohome

It provides services to https://github.com/zxdavb/evohome_cc, a Home Assistant integration

It requires a USB-to-RF device, either a Honeywell HGI80 (rare, expensive) or something running [evofw3](https://github.com/ghoti57/evofw3), such as the one from [here](https://indalo-tech.onlineweb.shop/).

## Installation

```
git clone https://github.com/zxdavb/ramses_rf
cd ramses_rf
pip install -r requirements.txt
```

You may want to clean up/create a virtual environment somewhere along the way, something like:
```
deactivate
rm -rf venv
python -m venv venv
. venv/bin/activate
pip install --upgrade pip
```

## Instructions

```
python client.py monitor /dev/ttyUSB0
```

Be sure to have a look at `-o packet_log.out` and `-p` (probe).
