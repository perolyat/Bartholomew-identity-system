# Development launcher (Windows). For a supervised, always-on service, run
#   python -m bartholomew serve
# under a service manager instead (see deploy/README.md) -- this script is a
# foreground process that dies with the terminal.
python -m bartholomew serve @args
