"""Composite, drift-tolerant machine fingerprint.

Five components, weighted, hashed individually so the server can compute a
similarity score on partial hardware swap without seeing raw values:

    motherboard_uuid  3
    cpu_id            2
    disk_serial       2
    machine_guid      2
    primary_mac       1

Any 7+/10 weight match = "same machine" on re-bind. Single hardware swap
(NIC, single disk) won't lock the user out.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from modules.dlc_pro.logger import get

log = get("license.fingerprint")


@dataclass
class FingerprintComponents:
    motherboard_uuid: str
    cpu_id: str
    disk_serial: str
    machine_guid: str
    primary_mac: str
    os_release: str

    def hashed(self) -> Dict[str, str]:
        """Return per-component SHA-256 hashes (server stores these)."""
        return {
            k: hashlib.sha256((v or "").encode("utf-8")).hexdigest()[:32]
            for k, v in self.__dict__.items()
        }

    def composite(self) -> str:
        """Single canonical hash for the whole machine."""
        canon = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- WMI helpers


def _wmic(arg: str, prop: str) -> str:
    try:
        out = subprocess.run(
            ["wmic", arg, "get", prop],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
        for line in out.splitlines()[1:]:
            line = line.strip()
            if line:
                return line
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _powershell(cmd: str) -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=8, check=False,
        ).stdout.strip()
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


# ---------------------------------------------------------------- per-component


def _motherboard_uuid() -> str:
    if sys.platform == "win32":
        v = _wmic("csproduct", "UUID") or _powershell(
            "(Get-CimInstance Win32_ComputerSystemProduct).UUID"
        )
        if v and v.lower() not in ("ffffffff-ffff-ffff-ffff-ffffffffffff", "00000000-0000-0000-0000-000000000000"):
            return v
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], timeout=5, text=True
            )
            m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out)
            if m:
                return m.group(1)
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
    elif sys.platform.startswith("linux"):
        for p in ("/sys/class/dmi/id/product_uuid", "/etc/machine-id"):
            try:
                with open(p, encoding="utf-8") as f:
                    v = f.read().strip()
                    if v:
                        return v
            except (FileNotFoundError, PermissionError):
                continue
    return ""


def _cpu_id() -> str:
    # Stable across reboots; vendor + brand + features signature.
    parts = [platform.processor() or "", platform.machine() or ""]
    if sys.platform == "win32":
        parts.append(_wmic("cpu", "ProcessorId"))
    elif sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        parts.append(line.split(":", 1)[1].strip())
                        break
        except FileNotFoundError:
            pass
    return "|".join(p for p in parts if p)


def _disk_serial() -> str:
    if sys.platform == "win32":
        # System drive's physical disk serial
        v = _powershell(
            "(Get-PhysicalDisk | Where-Object {$_.DeviceId -eq 0}).SerialNumber"
        )
        if v:
            return v.strip()
        return _wmic("diskdrive", "SerialNumber")
    elif sys.platform.startswith("linux"):
        try:
            out = subprocess.check_output(
                ["lsblk", "-no", "SERIAL", "-d"], timeout=5, text=True
            )
            for line in out.splitlines():
                line = line.strip()
                if line:
                    return line
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    return ""


def _machine_guid() -> str:
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as k:
                v, _ = winreg.QueryValueEx(k, "MachineGuid")
                return str(v)
        except (ImportError, OSError):
            return ""
    return ""


def _primary_mac() -> str:
    # uuid.getnode is stable for the primary MAC on most systems
    n = uuid.getnode()
    # If the OS couldn't resolve a real MAC, getnode sets bit 41
    if (n >> 40) & 0x01:
        return ""
    return ":".join(f"{(n >> i) & 0xFF:02x}" for i in (40, 32, 24, 16, 8, 0))


def collect() -> FingerprintComponents:
    fp = FingerprintComponents(
        motherboard_uuid=_motherboard_uuid(),
        cpu_id=_cpu_id(),
        disk_serial=_disk_serial(),
        machine_guid=_machine_guid(),
        primary_mac=_primary_mac(),
        os_release=f"{platform.system()} {platform.release()} {platform.version()}",
    )
    log.debug("fingerprint composite=%s", fp.composite()[:12])
    return fp
