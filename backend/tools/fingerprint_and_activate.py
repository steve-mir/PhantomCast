#!/usr/bin/env python3
"""
Compute a stable hardware fingerprint for this machine and (optionally) call
v1_activate to bind a license key to the PC.

Reads the same component keys the backend's COMPONENT_WEIGHTS expects:
  motherboard_uuid (3), cpu_id (2), disk_serial (2), machine_guid (2), primary_mac (1)

Usage:
    python fingerprint_and_activate.py
        # just print the fingerprint, no network call

    python fingerprint_and_activate.py --license-key PC-XXXXXXXXXXXXXXXX
        # also POST to v1_activate and print the response

    python fingerprint_and_activate.py --license-key PC-... --endpoint https://...
        # override the activation endpoint (defaults to diivix1)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from urllib import error, request

DEFAULT_ENDPOINT = "https://us-central1-diivix1.cloudfunctions.net/v1_activate"
COMPONENT_KEYS = ("motherboard_uuid", "cpu_id", "disk_serial", "machine_guid", "primary_mac")


def _run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()


def _darwin_components() -> dict[str, str]:
    ioreg = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    plat_uuid   = _re_extract(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', ioreg)
    plat_serial = _re_extract(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', ioreg)

    try:    cpu_family = _run(["sysctl", "-n", "hw.cpufamily"])
    except Exception: cpu_family = ""
    try:    cpu_brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    except Exception: cpu_brand = ""

    disk_info = _run(["diskutil", "info", "/"])
    vol_uuid  = _re_extract(r"Volume UUID:\s*([0-9A-Fa-f-]+)", disk_info)
    if not vol_uuid:
        vol_uuid = _re_extract(r"Disk / Partition UUID:\s*([0-9A-Fa-f-]+)", disk_info)

    primary_mac = ""
    for ifname in ("en0", "en1"):
        try:
            ifc = _run(["ifconfig", ifname])
            m = re.search(r"ether\s+([0-9a-f:]+)", ifc, re.I)
            if m:
                primary_mac = m.group(1).lower()
                break
        except Exception:
            continue

    return {
        "motherboard_uuid": plat_uuid,
        "cpu_id":           f"{cpu_brand}|{cpu_family}".strip("|"),
        "disk_serial":      vol_uuid,
        "machine_guid":     plat_serial,
        "primary_mac":      primary_mac,
    }


def _linux_components() -> dict[str, str]:
    def read(path: str) -> str:
        try:
            with open(path, "r") as f: return f.read().strip()
        except Exception:
            return ""

    return {
        "motherboard_uuid": read("/sys/class/dmi/id/product_uuid") or read("/etc/machine-id"),
        "cpu_id":           _re_extract(r"model name\s*:\s*(.+)", read("/proc/cpuinfo")),
        "disk_serial":      _re_extract(r"UUID=([0-9a-fA-F-]+)", _safe(["findmnt", "-no", "SOURCE,UUID", "/"])),
        "machine_guid":     read("/etc/machine-id"),
        "primary_mac":      _safe(["sh", "-c", "cat /sys/class/net/$(ip route show default | awk '/default/ {print $5; exit}')/address"]).lower(),
    }


def _windows_components() -> dict[str, str]:
    def wmic(query: str, field: str) -> str:
        try:
            out = subprocess.check_output(
                ["wmic", *query.split(), "get", field, "/value"],
                text=True, stderr=subprocess.DEVNULL,
            )
            m = re.search(rf"{field}=(.+)", out)
            return m.group(1).strip() if m else ""
        except Exception:
            return ""

    return {
        "motherboard_uuid": wmic("csproduct", "UUID"),
        "cpu_id":           wmic("cpu", "ProcessorId"),
        "disk_serial":      wmic("diskdrive", "SerialNumber"),
        "machine_guid":     wmic("csproduct", "IdentifyingNumber"),
        "primary_mac":      wmic("nic where (NetEnabled=true)", "MACAddress").lower(),
    }


def _re_extract(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def _safe(cmd: list[str]) -> str:
    try: return _run(cmd)
    except Exception: return ""


def collect_components() -> dict[str, str]:
    sysname = platform.system().lower()
    if sysname == "darwin":  return _darwin_components()
    if sysname == "linux":   return _linux_components()
    if sysname == "windows": return _windows_components()
    raise SystemExit(f"unsupported OS: {sysname}")


def fingerprint_hash(components: dict[str, str]) -> str:
    """Composite hash matching modules/phantom_cast/license/fingerprint.py — JSON
    dump of the components dict with sorted keys, sha256."""
    canon = json.dumps(components, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def hashed_components(components: dict[str, str]) -> dict[str, str]:
    """Per-component sha256 (first 32 hex chars) — what the desktop client
    sends to the backend so raw IDs never leave the machine in plaintext."""
    return {
        k: hashlib.sha256((components.get(k, "") or "").encode("utf-8")).hexdigest()[:32]
        for k in COMPONENT_KEYS
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--license-key", help="If set, POST to v1_activate.")
    p.add_argument("--endpoint",    default=DEFAULT_ENDPOINT, help=f"Activation endpoint (default: {DEFAULT_ENDPOINT}).")
    p.add_argument("--json",        action="store_true", help="Emit machine-readable JSON only.")
    args = p.parse_args()

    components = collect_components()
    fp = fingerprint_hash(components)
    components_hashed = hashed_components(components)

    if args.json:
        print(json.dumps({"components": components, "components_hashed": components_hashed, "fingerprint": fp}, indent=2))
    else:
        print("Hardware components")
        print("-------------------")
        for k in COMPONENT_KEYS:
            v = components.get(k, "")
            shown = v if v else "(empty — fingerprint will be weaker)"
            print(f"  {k:17s} = {shown}")
        print(f"\nFingerprint hash: {fp}\n")

    if not args.license_key:
        return 0

    body = json.dumps({
        "license_key":    args.license_key,
        "fingerprint":    fp,
        "components":     components_hashed,  # send hashes, not raw IDs
        "os":             platform.platform(),
        "client_version": "0.1.0-dev",
    }).encode("utf-8")

    print(f"POST {args.endpoint}")
    req = request.Request(args.endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=15) as r:
            payload = r.read().decode("utf-8")
            print(f"HTTP {r.status}")
            try:    print(json.dumps(json.loads(payload), indent=2))
            except Exception: print(payload)
    except error.HTTPError as e:
        payload = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        print(f"HTTP {e.code}")
        try:    print(json.dumps(json.loads(payload), indent=2))
        except Exception: print(payload)
        return 1
    except Exception as e:
        print(f"REQUEST FAILED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
