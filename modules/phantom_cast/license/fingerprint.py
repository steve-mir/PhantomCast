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

from modules.phantom_cast.logger import get

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
    """Legacy WMI command-line. wmic.exe was removed from Windows 11 24H2+ —
    callers must treat empty as "tool unavailable" and fall through to
    :func:`_powershell` (Get-CimInstance) for modern boxes."""
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
    """Run a PowerShell expression, return the first non-blank stripped line.

    Multi-line output (e.g. one row per disk) is collapsed to the first
    non-blank line so callers don't get whitespace-padded multi-disk junk.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=8, check=False,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    for line in out.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


# ---------------------------------------------------------------- per-component


def _motherboard_uuid() -> str:
    if sys.platform == "win32":
        # PowerShell first — wmic is gone on Win11 24H2+.
        v = _powershell(
            "(Get-CimInstance Win32_ComputerSystemProduct).UUID"
        ) or _wmic("csproduct", "UUID")
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


def _sysctl(name: str) -> str:
    try:
        return subprocess.check_output(
            ["sysctl", "-n", name], timeout=5, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return ""


def _cpu_id() -> str:
    # Stable across reboots; vendor + brand + features signature.
    if sys.platform == "darwin":
        # Match the format the desktop fingerprint tool writes:
        # "<machdep.cpu.brand_string>|<hw.cpufamily>".
        brand = _sysctl("machdep.cpu.brand_string")
        family = _sysctl("hw.cpufamily")
        return "|".join(p for p in (brand, family) if p)
    parts = [platform.processor() or "", platform.machine() or ""]
    if sys.platform == "win32":
        # PowerShell first — wmic is gone on Win11 24H2+.
        proc_id = _powershell(
            "(Get-CimInstance Win32_Processor | Select-Object -First 1).ProcessorId"
        ) or _wmic("cpu", "ProcessorId")
        if proc_id:
            parts.append(proc_id)
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
        # Boot-volume disk's serial. On modern Windows this is best resolved
        # by walking from the OS partition back to its physical disk so we
        # don't get tripped up by removable drives or USB sticks plugged
        # in at boot.
        cmd = (
            "$bootDrive = (Get-CimInstance Win32_OperatingSystem).SystemDrive; "
            "$part = Get-Partition -DriveLetter ($bootDrive.TrimEnd(':')) -ErrorAction SilentlyContinue; "
            "if ($part) { (Get-PhysicalDisk -DeviceNumber $part.DiskNumber -ErrorAction SilentlyContinue).SerialNumber } "
            "else { (Get-PhysicalDisk | Sort-Object DeviceId | Select-Object -First 1).SerialNumber }"
        )
        v = _powershell(cmd)
        if v:
            return v
        # Fallback for older Windows / restricted environments.
        v = _powershell("(Get-PhysicalDisk | Sort-Object DeviceId | Select-Object -First 1).SerialNumber")
        if v:
            return v
        return _wmic("diskdrive", "SerialNumber")
    elif sys.platform == "darwin":
        # Volume UUID of the boot volume — survives APFS snapshots & OS
        # reinstalls that keep the data volume in place. Reformat will
        # change it, but the similarity score absorbs single-component
        # drift.
        try:
            out = subprocess.check_output(
                ["diskutil", "info", "/"], timeout=5, text=True,
            )
            m = re.search(r"Volume UUID:\s*([0-9A-Fa-f-]+)", out)
            if m:
                return m.group(1)
            m = re.search(r"Disk / Partition UUID:\s*([0-9A-Fa-f-]+)", out)
            if m:
                return m.group(1)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        return ""
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
    if sys.platform == "darwin":
        # Apple-issued IOPlatformSerialNumber — survives OS reinstall and
        # account changes, only changes on hardware swap.
        try:
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                timeout=5, text=True,
            )
            m = re.search(r'"IOPlatformSerialNumber" = "([^"]+)"', out)
            if m:
                return m.group(1)
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
    return ""


def _normalize_mac(s: str) -> str:
    """Normalize a MAC string into ``aa:bb:cc:dd:ee:ff`` (lowercase, colon-sep).

    Accepts colon, dash, dot, or no separator.
    """
    if not s:
        return ""
    hexs = re.sub(r"[^0-9a-fA-F]", "", s).lower()
    if len(hexs) != 12:
        return ""
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2))


def _primary_mac() -> str:
    # macOS: prefer en0's MAC explicitly. uuid.getnode() can pick a virtual
    # interface (utun, awdl) whose MAC drifts between boots — that would
    # show up as a fingerprint mismatch on every restart.
    if sys.platform == "darwin":
        for ifname in ("en0", "en1"):
            try:
                out = subprocess.check_output(
                    ["ifconfig", ifname], timeout=5, text=True,
                    stderr=subprocess.DEVNULL,
                )
                m = re.search(r"ether\s+([0-9a-f:]+)", out, re.IGNORECASE)
                if m:
                    return m.group(1).lower()
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
        # Fall through to uuid.getnode if no Ethernet interface found.

    if sys.platform == "win32":
        # uuid.getnode() commonly picks a virtual adapter (Hyper-V Default
        # Switch, WSL bridge, VPN, Docker NAT, VirtualBox host-only). Those
        # MACs change across boots and software installs — useless as a
        # fingerprint. Walk the physical Ethernet/Wi-Fi adapter list
        # ourselves, sorted by ifIndex so the same machine yields the same
        # answer every time.
        cmd = (
            "Get-NetAdapter -Physical -ErrorAction SilentlyContinue | "
            "Where-Object { $_.HardwareInterface -and $_.MacAddress } | "
            "Sort-Object ifIndex | "
            "Select-Object -First 1 -ExpandProperty MacAddress"
        )
        mac = _normalize_mac(_powershell(cmd))
        if mac:
            return mac
        # Fallback: enumerate physical NICs via WMI/CIM. This survives on
        # boxes where Get-NetAdapter is locked down (some Server SKUs).
        cmd = (
            "Get-CimInstance Win32_NetworkAdapter "
            "-Filter 'PhysicalAdapter=true AND MACAddress IS NOT NULL' "
            "-ErrorAction SilentlyContinue | "
            "Sort-Object Index | "
            "Select-Object -First 1 -ExpandProperty MACAddress"
        )
        mac = _normalize_mac(_powershell(cmd))
        if mac:
            return mac
        # If both PowerShell paths fail, fall through to uuid.getnode
        # rather than empty-string — at least we get *something* unique.

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
