# Root app.py to expose the FastAPI app as `app` for `uvicorn app:app`
try:
    from services.api.app import app  # noqa: F401
except Exception:
    # Provide a clear import-time error to the console to help debugging path issues.
    import sys
    import traceback

    traceback.print_exc()
    sys.stderr.write(
        "\n[bartholomew] Failed to import services.api.app. Ensure the repo root is on PYTHONPATH.\n",
    )
    raise
