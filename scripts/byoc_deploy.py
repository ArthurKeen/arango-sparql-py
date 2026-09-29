#!/usr/bin/env python3
"""Upload and deploy arango-sparql-py to the ArangoDB Container Manager (BYOC).

Companion to ``scripts/package_arango_manual.sh``, which builds the tarball this
uploads. Ported from arango-ontoextract's ``scripts/byoc_deploy.py`` (the
platform protocol is identical across projects); the differences are this
project's env-var names (``ARANGO_URL`` not ``ARANGO_ENDPOINT``), its version
source (``arango_sparql/__init__.py``), and a Vite/relative-base UI (so the
asset checks look for relative bundles, not a baked mount prefix).

Usage
-----
    python3 scripts/byoc_deploy.py list
    python3 scripts/byoc_deploy.py update                  # the usual one
    python3 scripts/byoc_deploy.py verify --expect-version 0.2.0
    python3 scripts/byoc_deploy.py rollback --to 0.2.0-1
    python3 scripts/byoc_deploy.py delete
    python3 scripts/byoc_deploy.py status --service-id arango-user-defined-xxxxx

``update`` is the whole job: pre-flight the tarball, upload it under a version
derived from ``arango_sparql/__init__.py``, swap the live service, then prove the
new code is actually serving.

**There is no in-place update** on this platform: a POST against a live
``app_instance_name`` fails with a Helm ServiceAccount ownership error, so an
update DELETES then RECREATES and the service is briefly gone (~60s cold start on
py12base). Upload happens before the delete, so a bad artifact fails while the
old service still serves.

Config comes from the repo-root ``.env`` (``ARANGO_URL``, ``ARANGO_USER``,
``ARANGO_PASSWORD``, ``ARANGO_DB``) and can be overridden per flag. Nothing here
writes a token to disk.
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARBALL = REPO_ROOT / "arango-sparql-py.tar.gz"

DEFAULT_APP_NAME = "arango-sparql-py"
DEFAULT_INSTANCE = "arango-sparql-py"
# py13base is the documented house standard but is NOT on every cluster
# (prod.demo.pilot.arango.ai offers node22base, py12base, py12cugraph, py12torch).
# A wrong key fails fast and the error lists the valid ones. sparql-py is
# requires-python >=3.11, so py12base is in range; it ships no uv and the
# entrypoint falls back to ensurepip.
DEFAULT_BASE_IMAGE = "py12base"
DEFAULT_DISPLAY_NAME = "Arango SPARQL Transpiler"
DEFAULT_DESCRIPTION = "SPARQL 1.1 → AQL translation service + workbench for ArangoDB."

ACP = "/_platform/acp/v1"
FILEMANAGER = "/_platform/filemanager/global/byoc/"

READY = {"DEPLOYED"}
FAILED = {"FAILED", "ERROR", "TERMINATED"}


class DeployError(RuntimeError):
    pass


def load_env(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a plain dict, stripping surrounding quotes.

    A quoted password that keeps its quotes surfaces as a 401 that points nowhere
    near the cause, so we strip them the way the platform does.
    """
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):  # shell-style `export KEY=val`
            line = line[len("export ") :].lstrip()
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class Platform:
    """Thin client over the Container Manager endpoints a BYOC release uses."""

    def __init__(self, base: str, user: str, password: str, *, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.user = user
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self._jwt: str | None = None

    def authenticate(self) -> None:
        response = self.session.post(
            f"{self.base}/_open/auth",
            json={"username": self.user, "password": self.password},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise DeployError(f"auth failed: HTTP {response.status_code}")
        token = response.json().get("jwt")
        if not token:
            raise DeployError("auth response carried no 'jwt' field")
        self._jwt = token

    def _headers(self) -> dict[str, str]:
        if self._jwt is None:
            self.authenticate()
        return {"Authorization": f"Bearer {self._jwt}"}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        url = f"{self.base}{path}"
        response = self.session.request(method, url, headers=self._headers(), **kwargs)
        if response.status_code == 401:  # one transparent re-auth for a slow call
            self.authenticate()
            response = self.session.request(method, url, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            raise DeployError(f"{method} {path} -> HTTP {response.status_code}: {response.text[:400]}")
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text[:400]}

    def list_packages(self) -> list[dict]:
        return self._request("GET", FILEMANAGER).get("services", [])

    def list_services(self) -> list[dict]:
        return self._request("POST", f"{ACP}/list_services", json={}).get("services", [])

    def upload(self, tarball: Path, name: str, version: str) -> dict:
        """Upload the package. The platform keys on (name, version) and rejects a reuse."""
        with tarball.open("rb") as handle:
            return self._request(
                "POST",
                FILEMANAGER,
                data={"name": name, "version": version, "language": "python", "type": "Service"},
                files={"file": (tarball.name, handle, "application/gzip")},
                timeout=600,
            )

    def deploy(
        self,
        name: str,
        version: str,
        instance: str,
        db_name: str | None,
        base_image: str,
        *,
        has_ui: bool = False,
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict:
        # Every value must be a string: the platform decodes `env` as a protobuf
        # string->string map and rejects a JSON boolean ("invalid value for
        # string field value: true").
        env: dict[str, str] = {
            "service_type": "base_type",
            "base_image": base_image,
            "app_instance_name": instance,
        }
        if db_name:  # omitting db_name mounts under _global instead of _db/<db>
            env["db_name"] = db_name
        if has_ui:  # advertise the bundled UI so the platform presents it as an app
            env["has_ui"] = "true"
        if display_name:
            env["display_name"] = display_name
        if description:
            env["description"] = description
        return self._request(
            "POST",
            f"{ACP}/uds",
            json={"app_name": name, "app_version": version, "env": env},
            timeout=180,
        )

    def service_status(self, service_id: str) -> dict:
        return self._request("GET", f"{ACP}/service/{service_id}")

    def delete_service(self, service_id: str) -> None:
        self._request("DELETE", f"{ACP}/service/{service_id}", timeout=120)

    def find_instances(self, instance: str) -> list[dict]:
        """Every deployed service running under ``app_instance_name``."""
        found = []
        for service in self.list_services():
            uds = ((service.get("serviceMeta") or {}).get("udsMeta")) or {}
            if uds.get("appInstanceName") == instance:
                found.append(
                    {
                        "serviceId": service.get("serviceId"),
                        "version": uds.get("version"),
                        "status": service.get("status"),
                        "dbName": service.get("dbName"),
                    }
                )
        return found

    def resolve_instance(self, instance: str) -> dict | None:
        """Exactly one service for this instance name, or None. Refuses ambiguity."""
        matches = self.find_instances(instance)
        if len(matches) > 1:
            raise DeployError(
                f"{len(matches)} services are running as instance {instance!r}: "
                f"{[m['serviceId'] for m in matches]}. Delete the extras by id first."
            )
        return matches[0] if matches else None

    def wait_until_ready(
        self, service_id: str, *, timeout_s: float = 600.0, interval_s: float = 10.0
    ) -> dict:
        deadline = time.monotonic() + timeout_s
        last: dict = {}
        while time.monotonic() < deadline:
            last = self.service_status(service_id)
            info = last.get("serviceInfo") if isinstance(last, dict) else {}
            if not isinstance(info, dict):
                info = {}
            state = str(info.get("status") or last.get("status") or "").upper()
            if state in READY:
                return last
            if state in FAILED:
                raise DeployError(f"service {service_id} reached {state}: {last}")
            print(f"    status={state or '(unknown)'} — waiting {interval_s:.0f}s", flush=True)
            time.sleep(interval_s)
        raise DeployError(f"timed out after {timeout_s:.0f}s; last status: {last}")


def mount_path(instance: str, db_name: str | None) -> str:
    """The public prefix the platform serves this instance under.

    Set the service's ``ROOT_PATH`` env to this value so FastAPI serves
    openapi.json / the API under the same prefix the UI is mounted at.
    """
    scope = f"_db/{db_name}" if db_name else "_global"
    return f"/_service/uds/{scope}/{instance}"


def _service_id_of(result: dict) -> tuple[str | None, str | None]:
    """Pull (serviceId, status) out of a deploy/status response (nested in serviceInfo)."""
    info = result.get("serviceInfo") if isinstance(result, dict) else None
    if not isinstance(info, dict):
        info = result if isinstance(result, dict) else {}
    service_id = info.get("serviceId") or info.get("service_id")
    return service_id, info.get("status")


def read_app_version() -> str:
    """The canonical release version, from ``arango_sparql/__init__.py``.

    Parsed (not imported) so this script needs no venv. Kept in lockstep with
    pyproject via tests/test_version_parity.py.
    """
    text = (REPO_ROOT / "arango_sparql" / "__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not match:
        raise DeployError("no __version__ found in arango_sparql/__init__.py")
    return match.group(1)


def _version_sort_key(version: str) -> tuple:
    """Natural sort for ``<release>-<build>`` strings so 0.2.0-10 sorts after
    0.2.0-2 (plain ``sorted`` is lexicographic and buries the newest build).

    Each segment becomes ``(0, int)`` or ``(1, str)`` so a numeric and a
    non-numeric segment at the same position never compare int-vs-str (which
    would raise ``TypeError`` on a stray tag like ``0.2.0rc1``).
    """
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.-]", version))


def _url_is_loopback(url: str) -> bool:
    """True iff *url*'s host is exactly a loopback address (host-parsed, not a
    substring match). Kept in sync with ``entrypoint._is_loopback`` so preflight
    rejects the same set the runtime does — otherwise a bad ``.env`` passes
    preflight and only fails after the live service is already deleted."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if not host and url:
        host = url.split("//")[-1].split("/")[0].rsplit(":", 1)[0].strip("[]").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def next_build_version(platform: Platform, name: str, release: str) -> str:
    """``<release>-<n>``, the first suffix not already uploaded.

    The platform keys stored packages on (name, version) and refuses to overwrite
    one, so a redeploy of the same release needs a distinct string. Deriving from
    the release keeps every deployed artifact traceable to a real version.
    """
    taken = {p.get("version") for p in platform.list_packages() if p.get("name") == name}
    for build in range(1, 1000):
        candidate = f"{release}-{build}"
        if candidate not in taken:
            return candidate
    raise DeployError(f"no free build suffix for {release} (999 taken?)")


def preflight(tarball: Path, has_ui: bool) -> None:
    """Refuse to upload an artifact that cannot work."""
    if not tarball.exists():
        raise DeployError(f"no tarball at {tarball} — run scripts/package_arango_manual.sh first")
    problems: list[str] = []
    with tarfile.open(tarball, "r:gz") as archive:
        names = archive.getnames()
        normalised = {n.lstrip("./") for n in names}

        # Flat layout: a nested tree fails inside the platform as "No entrypoint found".
        for required in ("entrypoint", "pyproject.toml", "arango_sparql/__init__.py"):
            if required not in normalised:
                problems.append(f"{required} is not at the archive root")

        def read(member: str) -> str:
            for candidate in (member, f"./{member}"):
                if candidate in names:
                    handle = archive.extractfile(candidate)
                    if handle:
                        return handle.read().decode("utf-8", "replace")
            return ""

        # UI must actually be in the bundle when we intend to deploy has_ui.
        if has_ui and "ui/dist/index.html" not in normalised:
            problems.append(
                "ui/dist/index.html missing but deploying with a UI — rebuild the "
                "tarball with the UI (default) or deploy --no-ui"
            )

        env_text = read(".env")
        if not env_text:
            print(
                "    note: no .env baked — the service must get ARANGO_URL / "
                "ARANGO_USER / ARANGO_PASSWORD from Container Manager env vars "
                "(bake it instead with PACKAGE_INCLUDE_ENV=1)",
                file=sys.stderr,
            )
        else:
            url_match = re.search(r"^(?:export\s+)?ARANGO_URL\s*=\s*(\S+)", env_text, re.M)
            if url_match and _url_is_loopback(url_match.group(1).strip("\"'")):
                problems.append(
                    "ARANGO_URL points at loopback in the baked .env — unreachable from inside the platform"
                )
    if problems:
        raise DeployError("pre-flight failed:\n  - " + "\n  - ".join(problems))
    print("    pre-flight OK (flat layout, package present, UI bundle, baked-env sanity)")


def resolve_config(args: argparse.Namespace) -> tuple[Platform, str, str | None]:
    env = load_env(REPO_ROOT / ".env")
    # ARANGO_URL is this project's canonical name; accept ARANGO_ENDPOINT as a
    # cross-estate fallback so an ontoextract-style .env also works.
    endpoint = args.endpoint or env.get("ARANGO_URL") or env.get("ARANGO_ENDPOINT")
    user = env.get("ARANGO_USER") or env.get("ARANGO_USERNAME")
    password = env.get("ARANGO_PASSWORD")
    if not endpoint or not user or not password:
        raise DeployError(
            "need ARANGO_URL (or ARANGO_ENDPOINT), ARANGO_USER (or ARANGO_USERNAME) "
            "and ARANGO_PASSWORD in .env"
        )
    # `--db ''` is the explicit way to ask for global scope; absent means "use .env".
    db_name = env.get("ARANGO_DB") if args.db is None else (args.db or None)
    return Platform(endpoint, user, password), endpoint, db_name


def cmd_list(args: argparse.Namespace) -> int:
    platform, endpoint, _ = resolve_config(args)
    print(f"platform: {endpoint}\n")
    print("uploaded packages (most recent first):")
    for package in platform.list_packages()[:15]:
        name = str(package.get("name", "?"))
        version = str(package.get("version", "?"))
        print(f"  {name:<24} v{version:<14} {package.get('file_name', '')}")
    print("\ndeployed user-defined services:")
    for service in platform.list_services():
        meta = service.get("serviceMeta") or {}
        if str(meta.get("serviceType", "")).startswith("arango-user-defined"):
            # str() guard: a partially-created / terminated service can report
            # serviceId=None, and f"{None:<40}" raises TypeError.
            print(
                f"  {str(service.get('serviceId')):<40} db={service.get('dbName') or '(global)'} "
                f"status={service.get('status')}"
            )
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    platform, _, _ = resolve_config(args)
    tarball = Path(args.tarball)
    if not tarball.exists():
        raise DeployError(f"no tarball at {tarball} — run scripts/package_arango_manual.sh first")
    size_mb = tarball.stat().st_size / 1_048_576
    print(f"==> uploading {tarball.name} ({size_mb:.1f} MB) as {args.name} v{args.version}")
    print(f"    {platform.upload(tarball, args.name, args.version)}")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    platform, endpoint, db_name = resolve_config(args)
    prefix = mount_path(args.instance, db_name)
    print(f"==> deploying {args.name} v{args.version} as instance '{args.instance}'")
    print(f"    scope: {db_name or '(global)'}   base image: {args.base_image}")
    print(f"    will mount at: {prefix}/")
    if not args.no_ui:
        print(f"    UI on — set the service env ROOT_PATH={prefix} so the API aligns with the UI")
    result = platform.deploy(
        args.name,
        args.version,
        args.instance,
        db_name,
        args.base_image,
        has_ui=not args.no_ui,
        display_name=args.display_name,
        description=args.description,
    )
    service_id, state = _service_id_of(result)
    print(f"    serviceId={service_id} status={state}")
    if service_id and not args.no_wait:
        print("==> waiting for DEPLOYED...")
        platform.wait_until_ready(service_id, timeout_s=args.wait_timeout)
        print("    DEPLOYED")
    print(f"\n  {endpoint}{prefix}/")
    return 0


def _asset_urls(html: str, base_url: str, platform_base: str) -> list[str]:
    """Absolute URLs for every JS/CSS asset the page references.

    The Vite build uses base:"./" so refs are relative (``./assets/x.js`` or
    ``assets/x.js``); we resolve those against the served page URL. Absolute
    (``/…``) refs resolve against the platform host. External (``http…``) refs
    are skipped — nothing on this deployment to verify.
    """
    refs = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', html)
    out: list[str] = []
    for ref in dict.fromkeys(refs):
        if ref.startswith(("http://", "https://")):
            continue
        if ref.startswith("/"):
            out.append(platform_base + ref)
        else:
            out.append(base_url + ref.lstrip("./"))
    return out


def deep_verify(platform: Platform, url: str, expect_version: str | None, *, expect_ui: bool = True) -> bool:
    """Prove the *right code* is live, not merely that something answered.

    A 200 on the root cannot tell a new deployment from the old one; the version
    in ``openapi.json`` (from ``arango_sparql/__init__.py`` via the FastAPI
    constructor) can. UI assets are fetched too, because a broken bundle leaves
    the root serving and every asset 404ing — a blank screen with a green light.
    """
    ok = True
    try:
        spec = platform.session.get(url + "openapi.json", headers=platform._headers(), timeout=30).json()
        version = spec.get("info", {}).get("version")
        print(f"    openapi version  {version}   routes {len(spec.get('paths', {}))}")
        if expect_version and version != expect_version:
            print(
                f"    FAIL: live version is {version}, expected {expect_version} "
                "— the old build is still serving",
                file=sys.stderr,
            )
            ok = False
    except Exception as exc:
        # Fail closed when a version was demanded: an unreadable openapi.json means
        # we CANNOT prove the right build is live (commonly ROOT_PATH is unset so
        # the API isn't under the mount prefix). Only downgrade to a note when no
        # version assertion was requested (e.g. rollback, which reports its own).
        message = (
            f"openapi.json not readable at {url} ({exc}) — the API is likely not "
            "under the mount prefix; set the service ROOT_PATH to the mount path"
        )
        if expect_version:
            print(f"    FAIL: {message} (cannot confirm version {expect_version})", file=sys.stderr)
            ok = False
        else:
            print(f"    note: {message}", file=sys.stderr)

    try:
        html = platform.session.get(url, headers=platform._headers(), timeout=30).text
        assets = _asset_urls(html, url, platform.base)
        if expect_ui and not assets:
            # A UI deploy whose root page references no JS/CSS is a broken/blank
            # bundle (or the wrong page) — a green light over an empty screen.
            print(
                "    FAIL: expected a UI but the root page references no JS/CSS assets "
                "— the bundle is missing or blank",
                file=sys.stderr,
            )
            ok = False
        broken = []
        for asset in assets:
            response = platform.session.get(asset, headers=platform._headers(), timeout=30)
            if response.status_code != 200:
                broken.append((response.status_code, asset))
        print(f"    UI assets        {len(assets) - len(broken)}/{len(assets)} served")
        for code, asset in broken[:5]:
            print(f"    FAIL: {code} {asset}", file=sys.stderr)
            ok = False
    except Exception as exc:
        print(f"    FAIL: could not check assets: {exc}", file=sys.stderr)
        ok = False

    try:
        health = platform.session.get(url + "health", headers=platform._headers(), timeout=30)
        print(f"    /health          {health.status_code} {health.text[:40]}")
        if health.status_code != 200:
            ok = False
    except Exception as exc:
        print(f"    FAIL: /health unreachable: {exc}", file=sys.stderr)
        ok = False

    print("    => VERIFIED" if ok else "    => VERIFICATION FAILED")
    return ok


def cmd_delete(args: argparse.Namespace) -> int:
    platform, _, _ = resolve_config(args)
    existing = platform.resolve_instance(args.instance)
    if not existing:
        print(f"no service is running as instance {args.instance!r} — nothing to delete")
        return 0
    print(f"==> deleting {existing['serviceId']} (instance {args.instance}, version {existing['version']})")
    platform.delete_service(existing["serviceId"])
    print("    deleted")
    return 0


def _swap(platform: Platform, args: argparse.Namespace, db_name: str | None, version: str) -> int:
    """Delete-then-create, which is what an update *is* on this platform.

    POST /uds against a live app_instance_name fails with a Helm ownership error,
    so there is no in-place update — the service is briefly gone (~60s cold start
    on py12base).
    """
    existing = platform.resolve_instance(args.instance)
    if existing:
        print(f"==> replacing {existing['serviceId']} (version {existing['version']} -> {version})")
        platform.delete_service(existing["serviceId"])
        print("    old service deleted — the URL is down from here")
    else:
        print(f"==> no existing instance {args.instance!r}; creating fresh")

    result = platform.deploy(
        args.name,
        version,
        args.instance,
        db_name,
        args.base_image,
        has_ui=not args.no_ui,
        display_name=args.display_name,
        description=args.description,
    )
    service_id, state = _service_id_of(result)
    print(f"    created {service_id} status={state}")
    args.expect_version = getattr(args, "expect_version", None)
    args.wait_timeout = getattr(args, "wait_timeout", 900.0)
    args.poll_interval = getattr(args, "poll_interval", 15.0)
    return cmd_verify(args)


def cmd_update(args: argparse.Namespace) -> int:
    """Build-free update: pre-flight, upload, swap, verify. Upload precedes delete."""
    platform, _endpoint, db_name = resolve_config(args)
    tarball = Path(args.tarball)
    release = read_app_version()
    print(f"==> release {release} (arango_sparql/__init__.py)")
    preflight(tarball, has_ui=not args.no_ui)

    version = args.version or next_build_version(platform, args.name, release)
    size_mb = tarball.stat().st_size / 1_048_576
    print(f"==> uploading {tarball.name} ({size_mb:.1f} MB) as {args.name} v{version}")
    platform.upload(tarball, args.name, version)
    print("    uploaded")

    args.expect_version = release
    return _swap(platform, args, db_name, version)


def cmd_rollback(args: argparse.Namespace) -> int:
    """Redeploy a package version already on the platform (code only, fast)."""
    platform, _endpoint, db_name = resolve_config(args)
    versions = {p.get("version") for p in platform.list_packages() if p.get("name") == args.name}
    versions.discard(None)
    available = sorted(versions, key=_version_sort_key)
    if args.to not in available:
        raise DeployError(
            f"{args.name} v{args.to} is not uploaded. Available (most recent 10): {available[-10:]}"
        )
    print(f"==> ROLLBACK to {args.to} (code only)")
    args.expect_version = None  # the old build reports its own release version
    return _swap(platform, args, db_name, args.to)


def cmd_verify(args: argparse.Namespace) -> int:
    """Poll the public URL until the pod actually serves. Trailing slash required."""
    platform, endpoint, db_name = resolve_config(args)
    platform.authenticate()
    url = f"{endpoint}{mount_path(args.instance, db_name)}/"
    # Poll /health, not the root: a bare-API (--no-ui) deploy registers no "/"
    # route, so polling the root would spin to timeout and report a false failure
    # for a healthy service. /health exists with or without the UI.
    probe = url + "health"
    print(f"==> polling {probe}")
    deadline = time.monotonic() + args.wait_timeout
    while time.monotonic() < deadline:
        try:
            response = platform.session.get(
                probe, headers=platform._headers(), timeout=30, allow_redirects=False
            )
            code = response.status_code
            if code == 200:
                print(f"    HTTP 200 — serving ({response.headers.get('content-type', '?')})")
                expect_ui = not getattr(args, "no_ui", False)
                return 0 if deep_verify(platform, url, args.expect_version, expect_ui=expect_ui) else 1
            hint = "route not registered" if code == 404 else "pod not ready"
            print(f"    HTTP {code} ({hint}) — retrying in {args.poll_interval:.0f}s", flush=True)
        except requests.RequestException as exc:
            print(f"    {type(exc).__name__} — retrying in {args.poll_interval:.0f}s", flush=True)
        time.sleep(args.poll_interval)
    print(f"error: {url} never returned 200 within {args.wait_timeout:.0f}s", file=sys.stderr)
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    platform, _, _ = resolve_config(args)
    print(platform.service_status(args.service_id))
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    cmd_upload(args)
    return cmd_deploy(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--endpoint", help="platform URL (default: ARANGO_URL)")
    parser.add_argument(
        "--db",
        default=None,
        help="database to scope to; pass an empty string for global scope (default: ARANGO_DB)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_artifact_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--name", default=DEFAULT_APP_NAME)
        p.add_argument("--version", required=True)
        p.add_argument("--tarball", default=str(DEFAULT_TARBALL))

    def add_deploy_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--instance", default=DEFAULT_INSTANCE)
        p.add_argument("--base-image", default=DEFAULT_BASE_IMAGE)
        p.add_argument(
            "--no-ui",
            action="store_true",
            help="deploy as a bare API; omit when the bundle carries ui/dist (default)",
        )
        p.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
        p.add_argument("--description", default=DEFAULT_DESCRIPTION)
        p.add_argument("--no-wait", action="store_true")
        # Generous by default: after a delete+recreate the pod cold-starts on
        # py12base (ensurepip + editable install of [service,nl]), which can take
        # a few minutes; a tighter poll would report a false failure mid-boot.
        p.add_argument("--wait-timeout", type=float, default=900.0)

    p_list = sub.add_parser("list", help="show uploaded packages and deployed services")
    p_list.set_defaults(func=cmd_list)

    p_upload = sub.add_parser("upload", help="upload the tarball as a package version")
    add_artifact_args(p_upload)
    p_upload.set_defaults(func=cmd_upload)

    p_deploy = sub.add_parser("deploy", help="deploy an already-uploaded version")
    add_artifact_args(p_deploy)
    add_deploy_args(p_deploy)
    p_deploy.set_defaults(func=cmd_deploy)

    p_verify = sub.add_parser("verify", help="poll the public URL until it serves")
    p_verify.add_argument("--instance", default=DEFAULT_INSTANCE)
    p_verify.add_argument("--wait-timeout", type=float, default=900.0)
    p_verify.add_argument("--poll-interval", type=float, default=20.0)
    p_verify.add_argument(
        "--no-ui",
        action="store_true",
        help="the deployment has no bundled UI — skip the root-page asset check",
    )
    p_verify.add_argument(
        "--expect-version",
        default=None,
        help="assert openapi.json reports this release (default: no assertion)",
    )
    p_verify.set_defaults(func=cmd_verify)

    p_update = sub.add_parser("update", help="pre-flight, upload, swap the live service, verify")
    p_update.add_argument("--name", default=DEFAULT_APP_NAME)
    p_update.add_argument(
        "--version", default=None, help="package version (default: <__version__>-<next free build>)"
    )
    p_update.add_argument("--tarball", default=str(DEFAULT_TARBALL))
    add_deploy_args(p_update)
    p_update.add_argument("--poll-interval", type=float, default=15.0)
    p_update.set_defaults(func=cmd_update)

    p_rollback = sub.add_parser("rollback", help="redeploy a previously uploaded version (code only)")
    p_rollback.add_argument("--name", default=DEFAULT_APP_NAME)
    p_rollback.add_argument("--to", required=True, help="package version to go back to")
    add_deploy_args(p_rollback)
    p_rollback.add_argument("--poll-interval", type=float, default=15.0)
    p_rollback.set_defaults(func=cmd_rollback)

    p_delete = sub.add_parser("delete", help="remove the service for an instance name")
    p_delete.add_argument("--instance", default=DEFAULT_INSTANCE)
    p_delete.set_defaults(func=cmd_delete)

    p_status = sub.add_parser("status", help="show one service's status")
    p_status.add_argument("--service-id", required=True)
    p_status.set_defaults(func=cmd_status)

    p_release = sub.add_parser("release", help="upload, then deploy, then wait")
    add_artifact_args(p_release)
    add_deploy_args(p_release)
    p_release.set_defaults(func=cmd_release)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
