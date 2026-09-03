"""The three allowlists every risky parameter must resolve through.

An allowlist is the difference between "open a URL" and "open *that* URL". All
three below share one shape and one refusal posture: **empty means nothing is
permitted**, never "everything is permitted". A deployment that forgot to
configure its application allowlist can launch no application, which is a
visible failure; the alternative -- an empty list read as no restriction -- is
the single most common way a capability model is defeated by a configuration
mistake.

They are plain frozen data. Loading them from the environment is
`bartholomew/windows_actuation/config.py`'s job on the device and the
enrolment's job on the server; this module only says what one *is* and what
resolving against it means, so both sides use the same rules.

The three
---------
* `ApplicationAllowlist`  -- `app_id -> absolute executable path`. The request
  names the key; the path is never in the request. This is what makes
  "no arbitrary executable path" structural rather than validated: there is
  nowhere in the wire format to put a path.
* `UrlDomainAllowlist`    -- registrable hostnames, matched exactly or as a
  parent of a subdomain. Not a regex and not a substring test: `evil-bank.com`
  does not match `bank.com`, and `bank.com.evil.net` does not either.
* `FilesystemRootAllowlist` -- absolute directory roots. A candidate path is
  fully resolved (symlinks included) before it is compared, so a symlink
  inside an allowed root that points outside it does not escape.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

#: Longest an application key may be. A key is an identifier chosen by the
#: operator, not content.
MAX_APP_ID = 64

#: Longest hostname the URL allowlist will hold or match.
MAX_HOSTNAME = 253


#: A path written in Windows syntax: a drive letter, or a UNC prefix. Detected
#: by *syntax*, not by the host platform, because the governing process may be
#: a Linux service validating a path that will be opened on a Windows desk PC.
#: `os.path` would answer that question about the machine it is running on,
#: which is the wrong machine.
#: A drive letter, with or without a separator after it. The separator is
#: optional on purpose: `normalise_path` reduces a drive root to `c:` (there is
#: no path component to keep), and a detector that then failed to recognise
#: `c:` as Windows syntax would compare it with the wrong separator and make a
#: drive-root allowlist entry match nothing beneath it.
_WINDOWS_PATH = re.compile(r"\A[A-Za-z]:")


def looks_like_windows_path(path: str) -> bool:
    """Whether `path` is written in Windows syntax, whatever host we are on."""
    text = str(path)
    return bool(_WINDOWS_PATH.match(text)) or text.startswith(("\\\\", "//"))


def is_absolute_path(path: str) -> bool:
    """Absolute in its *own* syntax, not in the running host's.

    `PurePath("C:\\Windows").is_absolute()` is False on Linux, which would make
    a Linux service reject every legitimate Windows path a device sent it. The
    syntax is chosen from the string, so both sides agree.
    """
    text = str(path)
    if looks_like_windows_path(text):
        return PureWindowsPath(text).is_absolute()
    return PurePosixPath(text).is_absolute()


def path_parts(path: str) -> tuple[str, ...]:
    """The components of a path, parsed in its own syntax."""
    text = str(path)
    pure = PureWindowsPath(text) if looks_like_windows_path(text) else PurePosixPath(text)
    return tuple(pure.parts)


def normalise_path(path: str) -> str:
    """One canonical spelling of a path, for comparison and for storage.

    Windows paths are lowercased (the filesystem is case-insensitive) and use a
    single backslash separator; POSIX paths keep their case. Both lose a
    trailing separator, so `C:\\data` and `C:\\data\\` are one string.

    Deliberately not `os.path.normcase`/`normpath`: those answer for the host,
    and the host is not always the machine the path belongs to.
    """
    text = str(path)
    if looks_like_windows_path(text):
        rendered = str(PureWindowsPath(text)).replace("/", "\\").lower()
        return rendered.rstrip("\\") or rendered
    rendered = str(PurePosixPath(text))
    return rendered.rstrip("/") or "/"


def _separator_for(normalised: str) -> str:
    return "\\" if looks_like_windows_path(normalised) else "/"


class AllowlistError(ValueError):
    """A value could not be resolved through the allowlist that governs it.

    Always a refusal. There is no code path that turns this into a warning and
    proceeds.
    """


def _normalise_hostname(raw: str) -> str:
    """Lowercase, strip a trailing dot, and refuse anything that is not a host.

    IDNA-encoded so that a Unicode homograph and its ASCII form compare as the
    same string rather than as two different allowlist entries.
    """
    host = str(raw).strip().rstrip(".").lower()
    if not host or len(host) > MAX_HOSTNAME:
        raise AllowlistError(f"{raw!r} is not a usable hostname")
    if any(c.isspace() for c in host) or "/" in host or "@" in host:
        raise AllowlistError(f"{raw!r} is not a usable hostname")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise AllowlistError(f"{raw!r} is not a usable hostname: {e}") from e
    return host


@dataclass(frozen=True)
class ApplicationAllowlist:
    """`app_id -> absolute executable path`. The only way to name a program.

    A request carries an `app_id`. The path it resolves to is operator
    configuration, so a caller -- including a compromised or model-driven one
    -- cannot name an executable that the operator did not already choose. The
    allowlist also has no place to put arguments, which is why
    `bartholomew/windows_actuation/win32.py`'s process starter takes exactly
    one parameter.
    """

    entries: Mapping[str, str]

    @classmethod
    def from_pairs(cls, pairs: Mapping[str, str] | None) -> ApplicationAllowlist:
        cleaned: dict[str, str] = {}
        for raw_key, raw_path in (pairs or {}).items():
            key = str(raw_key).strip().lower()
            if not key or len(key) > MAX_APP_ID:
                raise AllowlistError(f"{raw_key!r} is not a usable application key")
            if not key.replace("_", "").replace("-", "").replace(".", "").isalnum():
                raise AllowlistError(
                    f"application key {raw_key!r} may contain only letters, digits, "
                    "'.', '-' and '_'",
                )
            path = str(raw_path).strip()
            if not path:
                raise AllowlistError(f"application {key!r} has no executable path")
            if not is_absolute_path(path):
                raise AllowlistError(
                    f"application {key!r} must map to an absolute executable path, "
                    f"not {path!r}",
                )
            cleaned[key] = path
        return cls(entries=dict(cleaned))

    def __bool__(self) -> bool:
        return bool(self.entries)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.entries))

    def contains(self, app_id: str) -> bool:
        return str(app_id).strip().lower() in self.entries

    def resolve(self, app_id: str) -> str:
        """The executable path for `app_id`, or refuse.

        An empty allowlist refuses everything, and says so in those words:
        a deployment that has configured no applications can launch none.
        """
        key = str(app_id).strip().lower()
        if not self.entries:
            raise AllowlistError(
                "no applications are allowlisted on this device, so no application "
                "can be launched or focused. Configure BARTH_ACTION_APP_ALLOWLIST.",
            )
        try:
            return self.entries[key]
        except KeyError as e:
            raise AllowlistError(
                f"{app_id!r} is not an allowlisted application. The allowlisted "
                f"keys are {list(self.keys)}.",
            ) from e


@dataclass(frozen=True)
class UrlDomainAllowlist:
    """Hostnames a URL may point at. Exact match, or a parent of a subdomain."""

    hosts: frozenset[str]

    @classmethod
    def from_iterable(cls, raw: Iterable[str] | None) -> UrlDomainAllowlist:
        return cls(hosts=frozenset(_normalise_hostname(h) for h in (raw or ()) if str(h).strip()))

    def __bool__(self) -> bool:
        return bool(self.hosts)

    def permits(self, hostname: str) -> bool:
        """Whether `hostname` is allowlisted.

        Suffix matching is done on label boundaries, not on characters, so
        `notbank.com` never matches an allowlisted `bank.com` and
        `bank.com.attacker.net` never does either.
        """
        if not self.hosts:
            return False
        try:
            host = _normalise_hostname(hostname)
        except AllowlistError:
            return False
        if host in self.hosts:
            return True
        return any(host.endswith("." + allowed) for allowed in self.hosts)

    def require(self, hostname: str) -> str:
        host = _normalise_hostname(hostname)
        if not self.permits(host):
            raise AllowlistError(
                (
                    f"{host!r} is not an allowlisted URL domain. The allowlisted domains "
                    f"are {sorted(self.hosts)}."
                    if self.hosts
                    else (
                        "no URL domains are allowlisted on this device, so no URL can be "
                        "opened. Configure BARTH_ACTION_URL_ALLOWLIST."
                    )
                ),
            )
        return host


@dataclass(frozen=True)
class FilesystemRootAllowlist:
    """Absolute directory roots a path may lie inside. Opened, never written."""

    roots: tuple[str, ...]

    @classmethod
    def from_iterable(cls, raw: Iterable[str] | None) -> FilesystemRootAllowlist:
        roots: list[str] = []
        for entry in raw or ():
            text = str(entry).strip()
            if not text:
                continue
            if not is_absolute_path(text):
                raise AllowlistError(
                    f"filesystem root {text!r} must be absolute",
                )
            roots.append(normalise_path(text))
        return cls(roots=tuple(sorted(set(roots))))

    def __bool__(self) -> bool:
        return bool(self.roots)

    def contains(self, resolved_path: str) -> bool:
        """Whether an **already fully resolved** path lies inside a root.

        Takes a resolved path rather than resolving one itself, because
        resolution touches the filesystem and this type is pure data that both
        sides of the channel use. `require_within()` is the filesystem-aware
        entry point.
        """
        if not self.roots:
            return False
        target = normalise_path(resolved_path)
        for root in self.roots:
            if target == root:
                return True
            # The root plus exactly one separator, so "C:\\data" never matches
            # "C:\\database" -- a prefix test without the separator would.
            if target.startswith(root + _separator_for(root)):
                return True
        return False

    def require_within(self, path: str) -> str:
        """Fully resolve `path` and refuse it unless it lands inside a root.

        Resolution is the security-relevant step: `strict=True` means the
        target must exist, and every symlink and junction is followed *before*
        the comparison, so a link inside an allowed root that points elsewhere
        is refused rather than followed.
        """
        if not self.roots:
            raise AllowlistError(
                "no filesystem roots are allowlisted on this device, so no path can "
                "be opened. Configure BARTH_ACTION_PATH_ALLOWLIST.",
            )
        try:
            resolved = str(Path(path).resolve(strict=True))
        except (OSError, RuntimeError, ValueError) as e:
            raise AllowlistError(
                f"the path could not be resolved, so it cannot be checked against the "
                f"allowlisted roots: {type(e).__name__}",
            ) from e
        if not self.contains(resolved):
            raise AllowlistError(
                f"the resolved path is not inside any allowlisted root "
                f"({list(self.roots)}). Symbolic links are followed before this "
                f"check, so a link out of an allowed root does not escape it.",
            )
        return resolved
