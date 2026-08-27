#!/usr/bin/env bash

# Development launcher. For a supervised, always-on service use
#   python -m bartholomew serve
# under systemd/Docker instead (see deploy/README.md) -- this script is a
# foreground process that dies with the terminal.
exec python -m bartholomew serve "$@"
