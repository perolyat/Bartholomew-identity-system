#!/usr/bin/env python3
"""R-RETRIEVAL-1 control probe: the same three questions, either implementation.

Run it from a checkout of `main` and from the repaired branch and compare. It
deliberately does **not** import anything the repair added, and it discovers
whichever probe seam the tree it is running in happens to have, so the two runs
are the same experiment rather than two different ones.

    python3 docs/evidence/r-retrieval-1/fts5_availability_control.py

Questions asked, in the order the risk entry raises them:

  1. SCOPE     does one database's probe answer for another database?
  2. RECOVERY  does a single transient probe failure latch for the process?
  3. REPORTING does `describe_retrieval()` say anything about FTS at all?
"""

import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

os.environ.setdefault("BARTHO_EMBED_ALLOW_FALLBACK", "1")

from bartholomew.kernel import retrieval  # noqa: E402


def make_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, kind TEXT, "
        "key TEXT, value TEXT, summary TEXT, ts TEXT)",
    )
    conn.commit()
    conn.close()
    return path


def clear_cache():
    """Clear availability state, whichever form this tree keeps it in."""
    if hasattr(retrieval, "reset_fts5_cache"):
        retrieval.reset_fts5_cache()
    else:
        retrieval._fts5_available_cache = None


def unavailable_patch(kind):
    """Patch the tree's probe seam to report `kind` ("absent" or "transient").

    On `main` there is only one seam and only one answer it can give -- False --
    so both kinds collapse to the same patch. That collapse IS the defect.
    """
    if hasattr(retrieval, "probe_fts5"):
        # Imported here on purpose: this module must also import cleanly in a
        # tree that has no such names.
        from bartholomew.kernel.fts_client import FTS5ProbeResult, FTS5Status  # noqa: PLC0415

        result = (
            FTS5ProbeResult(FTS5Status.ABSENT, "no such module: fts5")
            if kind == "absent"
            else FTS5ProbeResult(FTS5Status.PROBE_ERROR, "OperationalError: database is locked")
        )
        return patch("bartholomew.kernel.retrieval.probe_fts5", return_value=result)
    return patch("bartholomew.kernel.retrieval.fts5_available", return_value=False)


def ask(db_path):
    """Is FTS usable for this database, as this tree answers it?"""
    if hasattr(retrieval, "check_fts5"):
        return retrieval.check_fts5(db_path).available
    return retrieval._check_fts5_once(db_path)


def main():
    seam = "probe_fts5 (repaired)" if hasattr(retrieval, "probe_fts5") else "fts5_available (main)"
    print(f"tree probe seam : {seam}")
    print(f"python          : {sys.version.split()[0]}\n")

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        db_a = make_db(os.path.join(tmp, "a.db"))
        db_b = make_db(os.path.join(tmp, "b.db"))

        # 1. SCOPE -------------------------------------------------------
        clear_cache()
        with unavailable_patch("absent"):
            ask(db_a)
        b_answer = ask(db_b)
        ok = b_answer is True
        failures.append(("SCOPE", ok))
        print("1. SCOPE     database A probed absent; B has FTS5")
        print(f"             B reports available = {b_answer}   -> {'PASS' if ok else 'FAIL'}")
        print("             FAIL means A's answer decided B's capability.\n")

        # 2. RECOVERY ----------------------------------------------------
        clear_cache()
        with unavailable_patch("transient"):
            first = ask(db_a)
        second = ask(db_a)
        ok = first is False and second is True
        failures.append(("RECOVERY", ok))
        print("2. RECOVERY  one transient probe failure, then a good probe")
        print(f"             first={first}  second={second}   -> {'PASS' if ok else 'FAIL'}")
        print("             FAIL means the failed probe latched for the process.\n")

        # 3. REPORTING ---------------------------------------------------
        clear_cache()
        try:
            described = retrieval.describe_retrieval()
        except TypeError:  # pragma: no cover - signature differences
            described = {}
        ok = "fts" in described
        failures.append(("REPORTING", ok))
        print("3. REPORTING describe_retrieval() keys")
        print(f"             {sorted(described)}")
        print(f"             carries an 'fts' field -> {'PASS' if ok else 'FAIL'}\n")

    bad = [name for name, ok in failures if not ok]
    print("RESULT:", "all three hold" if not bad else f"FAILS: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
