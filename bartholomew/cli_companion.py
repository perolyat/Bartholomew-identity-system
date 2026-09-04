"""Operator commands for the Windows companion: credential, observe, arm.

The narrowest control surface that makes the live path performable by a person
at the keyboard. Every command is a thin client over the loopback HTTP control
plane -- none of them decides anything, and none of them can grant authority
the server would not grant on its own. If a command appears to succeed, it is
because the server said so.

`bartholomew companion ...`

  credential store / show / forget   the device credential, in the OS keyring
  observe start / status / stop      one bounded observation session
  channel arm / status / disarm      the real Windows action channel

The commands live here rather than in `cli.py` for the reason `cli_trust.py`
gives: that file is a shared integration hotspot, and a handful of
registration lines is a smaller thing for every other stream to merge around
than several hundred.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

companion_app = typer.Typer(help="Windows companion: credential, observation, action channel")
credential_app = typer.Typer(help="The device credential, kept in the OS keyring")
observe_app = typer.Typer(help="Start, inspect and stop one observation session")
channel_app = typer.Typer(help="Arm, inspect and disarm the real Windows action channel")
companion_app.add_typer(credential_app, name="credential")
companion_app.add_typer(observe_app, name="observe")
companion_app.add_typer(channel_app, name="channel")

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _emit(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


def _credential(device_id: str | None) -> tuple[str, str]:
    """The stored credential, or exit with an instruction rather than a stack trace."""
    from bartholomew.platform.companion_credential import load

    found = load(device_id=device_id)
    if found is None:
        typer.echo(
            "No companion credential is configured on this machine. Store the one "
            "issued at enrolment with:\n"
            "  bartholomew companion credential store --device-id <DEVICE_ID>",
            err=True,
        )
        raise typer.Exit(code=2)
    return found


def _call(
    method: str,
    path: str,
    *,
    base_url: str,
    device_id: str | None = None,
    body: dict | None = None,
    authenticate: bool = True,
) -> int:
    """One loopback call. Returns the process exit code."""
    import requests

    from bartholomew.platform.device_inbound import DEVICE_CREDENTIAL_HEADER

    headers = {}
    if authenticate:
        resolved_device, secret = _credential(device_id)
        headers[DEVICE_CREDENTIAL_HEADER] = secret
        if body is not None and "device_id" in body and not body["device_id"]:
            body["device_id"] = resolved_device

    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://")):
        # The credential travels in this header. Sending it over plaintext HTTP
        # to anything but loopback would put a long-lived device secret on the
        # wire, so it is refused rather than warned about.
        typer.echo(
            f"Refusing to send a device credential to {base_url!r} over plaintext "
            "HTTP. Use loopback, or a URL with https://.",
            err=True,
        )
        return 2

    try:
        response = requests.request(
            method,
            f"{base_url.rstrip('/')}{path}",
            headers=headers,
            json=body,
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


# -- credential --------------------------------------------------------------


@credential_app.command("store")
def credential_store(
    device_id: str = typer.Option(..., "--device-id", help="The enrolled device id"),
) -> None:
    """Store this machine's device credential in the OS keyring.

    The secret is read from stdin, never from a command-line argument: an
    argument would land in the shell history and in the process table.
    """
    from bartholomew.platform.companion_credential import CredentialStoreError, store

    typer.echo("Paste the device credential issued at enrolment, then press Enter:", err=True)
    secret = sys.stdin.readline().strip()
    if not secret:
        typer.echo("No credential was entered; nothing was stored.", err=True)
        raise typer.Exit(code=2)
    try:
        store(device_id=device_id, secret=secret)
    except CredentialStoreError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Stored the credential for device {device_id} in the OS keyring.")


@credential_app.command("show")
def credential_show(
    device_id: str = typer.Option(None, "--device-id", help="The enrolled device id"),
) -> None:
    """Whether a credential is configured, and how well protected. Never its value."""
    from bartholomew.platform.companion_credential import describe

    _emit(describe(device_id=device_id))


@credential_app.command("forget")
def credential_forget(
    device_id: str = typer.Option(..., "--device-id", help="The enrolled device id"),
) -> None:
    """Remove this machine's stored credential."""
    from bartholomew.platform.companion_credential import forget

    removed = forget(device_id=device_id)
    typer.echo("Removed." if removed else "Nothing was stored for that device.")


# -- observation -------------------------------------------------------------


@observe_app.command("start")
def observe_start(
    modality: str = typer.Option("screen", help="screen, microphone or spoken_output"),
    window_id: str = typer.Option(None, help="Observe exactly this window"),
    window_title: str = typer.Option(None, help="Window title, for the status surface"),
    display_id: str = typer.Option(None, help="Observe exactly this display"),
    device_id: str = typer.Option(None, "--device-id"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    max_seconds: int = typer.Option(None, "--max-seconds"),
) -> None:
    """Ask to begin one bounded observation session.

    Bartholomew will ask you to confirm before anything is observed: the
    consent gate is interactive and fail-closed, and this command cannot
    answer it for you.
    """
    scope: dict | None = None
    if window_id:
        scope = {"kind": "window", "window_id": window_id, "window_title": window_title}
    elif display_id:
        scope = {"kind": "display", "display_id": display_id}

    body = {"modality": modality, "scope": scope}
    if max_seconds:
        body["max_duration_seconds"] = max_seconds
    raise typer.Exit(
        code=_call(
            "POST",
            "/api/multimodal/sessions",
            base_url=base_url,
            device_id=device_id,
            body=body,
        ),
    )


@observe_app.command("status")
def observe_status(base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url")) -> None:
    """What Bartholomew is observing right now, and how to stop it."""
    raise typer.Exit(
        code=_call("GET", "/api/multimodal/status", base_url=base_url, authenticate=False),
    )


@observe_app.command("stop")
def observe_stop(
    session_id: str = typer.Option(None, help="One session; omit to stop everything"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
) -> None:
    """Stop one session, or all of them. Needs no credential, by design."""
    path = (
        f"/api/multimodal/sessions/{session_id}/stop"
        if session_id
        else "/api/multimodal/sessions/stop-all"
    )
    raise typer.Exit(code=_call("POST", path, base_url=base_url, authenticate=False))


# -- action channel ----------------------------------------------------------


@channel_app.command("arm")
def channel_arm(
    device_id: str = typer.Option(None, "--device-id"),
    minutes: int = typer.Option(None, "--minutes", help="Up to 15; the default is 15"),
    reason: str = typer.Option(None, "--reason"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
) -> None:
    """Open the real Windows action channel for a bounded window.

    This authorises no action. Every action still needs its own explicit
    approval; arming only decides whether the machine may carry out anything
    at all right now.
    """
    body: dict = {"device_id": device_id or "", "reason": reason}
    if minutes:
        body["seconds"] = minutes * 60
    raise typer.Exit(
        code=_call(
            "POST",
            "/api/actions/channel/arm",
            base_url=base_url,
            device_id=device_id,
            body=body,
        ),
    )


@channel_app.command("status")
def channel_status(base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url")) -> None:
    """Whether the channel is armed, for how long, and for which device."""
    raise typer.Exit(
        code=_call("GET", "/api/actions/channel", base_url=base_url, authenticate=False),
    )


@channel_app.command("disarm")
def channel_disarm(base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url")) -> None:
    """Close the channel immediately. Needs no credential and is never refused."""
    raise typer.Exit(
        code=_call(
            "POST",
            "/api/actions/channel/disarm",
            base_url=base_url,
            authenticate=False,
        ),
    )


__all__ = ["companion_app"]
