"""CI-side test-execution machinery for Bartholomew.

Not application code and not imported by it. These modules implement the
Windows test-execution contract documented in
`docs/WINDOWS_TEST_EXECUTION_CONTRACT.md`: they own worker replacement,
accounting for work owned by a lost worker, and the diagnostics that
distinguish one failure mode from another.
"""
