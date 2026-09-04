"""`bartholomew consent` -- the person's side of a device observation ask.

When a companion asks to start observing, Bartholomew records a pending ask
and waits for a person. This is where the person answers. It deliberately
sends **no device credential**: the answer route refuses one, because a
machine that could answer "may this machine observe?" would make the question
meaningless.

The answer needs the ask's nonce, which lives only in the kernel database.
These commands read it from that database -- the same file the running server
uses, resolved the same way (`BARTH_DB_PATH`, else the project default) -- and
present it to the loopback server. Reading the database is what proves the
answer comes from the operator's own machine and account.
"""

from __future__ import annotations

import json
from typing import Any

import requests
import typer

from bartholomew.cli_companion import DEFAULT_BASE_URL
from bartholomew.kernel.db_paths import resolve_kernel_db_path
from bartholomew.multimodal import device_consent

consent_app = typer.Typer(help="Answer a device's request to start observing")

_DB_HELP = (
    "Kernel database. Default: BARTH_DB_PATH, else <project root>/data/barth.db "
    "-- the same file the running server reads."
)


def _emit(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


def _db(explicit: str | None) -> str:
    return resolve_kernel_db_path(explicit)


def _loopback_only(base_url: str) -> None:
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://")):
        typer.echo(
            f"Refusing to send a consent nonce to {base_url!r} over plaintext HTTP. "
            "Use loopback, or a URL with https://.",
            err=True,
        )
        raise typer.Exit(code=2)


@consent_app.command("pending")
def consent_pending(
    db: str = typer.Option(None, "--db", help=_DB_HELP),
) -> None:
    """The asks waiting for you, oldest first. Answer one with `approve` or `deny`."""
    path = _db(db)
    asks = device_consent.list_pending(path, include_nonce=False)
    if not asks:
        typer.echo("No device is waiting for your consent.")
        typer.echo(f"(database: {path})")
        raise typer.Exit(code=0)
    for item in asks:
        typer.echo(f"\n{item['request_id']}")
        typer.echo(f"  {item['prompt']}")
        typer.echo(f"  device:    {item['device_id']}")
        typer.echo(f"  modality:  {item['modality']}")
        typer.echo(f"  principal: {item['principal_id']}")
        typer.echo(f"  expires in {item['seconds_remaining']}s")
    typer.echo(
        "\nAnswer with:  bartholomew consent approve <request_id>   "
        "or  bartholomew consent deny <request_id>",
    )
    typer.echo(f"(database: {path})")


def _answer(request_id: str, approve: bool, db: str | None, base_url: str, note: str | None) -> int:
    _loopback_only(base_url)
    path = _db(db)
    match = [
        item
        for item in device_consent.list_pending(path, include_nonce=True)
        if item["request_id"] == request_id
    ]
    if not match:
        typer.echo(
            f"No open ask {request_id!r} in {path}. Run `bartholomew consent pending`.",
            err=True,
        )
        return 1
    nonce = match[0]["answer_nonce"]

    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/device-consent/{request_id}/answer",
            json={"nonce": nonce, "approve": approve, "note": note},
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001 - a CLI reports, it does not raise
        typer.echo(f"Could not reach Bartholomew at {base_url}: {e}", err=True)
        return 1
    try:
        _emit(response.json())
    except ValueError:
        typer.echo(response.text)
    return 0 if response.status_code < 400 else 1


@consent_app.command("approve")
def consent_approve(
    request_id: str = typer.Argument(..., help="From `bartholomew consent pending`"),
    db: str = typer.Option(None, "--db", help=_DB_HELP),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    note: str = typer.Option(None, "--note"),
) -> None:
    """Allow this one start attempt. Nothing is remembered for the next one."""
    raise typer.Exit(code=_answer(request_id, True, db, base_url, note))


@consent_app.command("deny")
def consent_deny(
    request_id: str = typer.Argument(..., help="From `bartholomew consent pending`"),
    db: str = typer.Option(None, "--db", help=_DB_HELP),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    note: str = typer.Option(None, "--note"),
) -> None:
    """Refuse this start attempt."""
    raise typer.Exit(code=_answer(request_id, False, db, base_url, note))


__all__ = ["consent_app"]
