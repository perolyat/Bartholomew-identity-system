"""`python -m bartholomew ...`.

Exists so a service unit file, a container CMD and a Windows service wrapper
can all name the same non-interactive entry point without depending on a
console script having been installed on PATH (`pip install -e .` is not a
safe assumption inside a systemd unit or a minimal image).
"""

from bartholomew.cli import main

if __name__ == "__main__":
    main()
