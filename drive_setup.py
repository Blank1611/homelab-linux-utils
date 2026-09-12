#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Homelab Storage Setup & Provisioning Tool

A generic, safe storage management and provisioning tool with pre-flight
action matrix evaluation, cascading lifecycle execution, smart partition
resolution, live subprocess streaming, and audit/discovery modes.
Uses strictly the Python 3 standard library.
"""

import argparse
import datetime
import getpass
import grp
import json
import logging
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# ==============================================================================
# TERMINAL CAPABILITIES & FORMATTING CONSOLE
# ==============================================================================
class Console:
    """
    Encapsulates terminal formatting, stream routing, TTY detection,
    and NO_COLOR environment compliance. Eliminates global state.
    """

    def __init__(self, color_mode: str = "auto", stream=None):
        self.stream = stream or sys.stdout
        self.color_mode = color_mode
        self._enabled = self._determine_color_enabled(self.stream)

    def _determine_color_enabled(self, target_stream) -> bool:
        if self.color_mode == "never":
            return False
        if self.color_mode == "always":
            return True
        if "NO_COLOR" in os.environ:
            return False
        return hasattr(target_stream, "isatty") and target_stream.isatty()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def bold(self, text: str) -> str:
        return f"\033[1m{text}\033[0m" if self._enabled else str(text)

    def green(self, text: str) -> str:
        return f"\033[32m{text}\033[0m" if self._enabled else str(text)

    def yellow(self, text: str) -> str:
        return f"\033[33m{text}\033[0m" if self._enabled else str(text)

    def red(self, text: str) -> str:
        return f"\033[31m{text}\033[0m" if self._enabled else str(text)

    def cyan(self, text: str) -> str:
        return f"\033[36m{text}\033[0m" if self._enabled else str(text)

    def blue(self, text: str) -> str:
        return f"\033[34m{text}\033[0m" if self._enabled else str(text)

    def dim(self, text: str) -> str:
        return f"\033[2m{text}\033[0m" if self._enabled else str(text)

    def print(self, *args, **kwargs) -> None:
        if "file" not in kwargs:
            kwargs["file"] = self.stream
        if "flush" not in kwargs:
            kwargs["flush"] = True
        print(*args, **kwargs)

    def print_error(self, *args, **kwargs) -> None:
        kwargs["file"] = sys.stderr
        if "flush" not in kwargs:
            kwargs["flush"] = True
        print(*args, **kwargs)

    def rule(self, char: str = "=", length: int = 135, title: str = "") -> None:
        if not title:
            self.print(self.bold(char * length))
        else:
            padding = max(0, (length - len(title) - 2) // 2)
            line = f"{char * padding} {title} {char * padding}"
            if len(line) < length:
                line += char * (length - len(line))
            self.print(self.bold(line))


# ==============================================================================
# LOGGING CONFIGURATION (STANDARD POSIX / PYTHON LOGGING)
# ==============================================================================
class StandardColoredFormatter(logging.Formatter):
    """
    Standard POSIX/Python log formatter with ANSI-colored level tags.
    Format: %(asctime)s [%(levelname)s] [tid:%(thread)d] [%(name)s]: %(message)s
    """

    def __init__(self, console: Console, fmt: Optional[str] = None, datefmt: Optional[str] = None):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.console = console

    def format(self, record: logging.LogRecord) -> str:
        if self.console.enabled:
            orig_levelname = record.levelname
            if record.levelno == logging.DEBUG:
                record.levelname = self.console.blue(orig_levelname)
            elif record.levelno == logging.INFO:
                record.levelname = self.console.green(orig_levelname)
            elif record.levelno == logging.WARNING:
                record.levelname = self.console.yellow(orig_levelname)
            elif record.levelno == logging.ERROR:
                record.levelname = self.console.red(orig_levelname)
            formatted = super().format(record)
            record.levelname = orig_levelname
            return formatted
        return super().format(record)


class UnbufferedStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes immediately on every emitted log record."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


logger = logging.getLogger("drive_setup")


def setup_logging(console: Optional[Console] = None, verbose: bool = True, quiet: bool = False, stream=None) -> None:
    """
    Configure standard logging. Defaults to verbose (DEBUG) for personal homelab use.
    In quiet mode, sets WARNING level to suppress routine probe logs.
    In JSON mode, stream is directed to sys.stderr.
    """
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logger.setLevel(level)

    active_console = console or Console(stream=stream or sys.stdout)
    output_stream = stream or active_console.stream
    handler = UnbufferedStreamHandler(output_stream)
    handler.setLevel(level)
    fmt = "%(asctime)s [%(levelname)s] [tid:%(thread)d] [%(name)s]: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handler.setFormatter(StandardColoredFormatter(console=active_console, fmt=fmt, datefmt=datefmt))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False


# Initialize default logging on module load
setup_logging(verbose=True)

# Compatibility aliases
log_info = logger.info
log_debug = logger.debug
log_warn = logger.warning
log_error = logger.error
log_step = logger.info


# ==============================================================================
# EXCEPTIONS
# ==============================================================================
class StorageSetupError(Exception):
    """Base exception for storage setup errors."""

    def __init__(self, message: str, error_code: str = "E_GENERAL", remediation: str = "", extra: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.remediation = remediation
        self.extra = extra or {}


class DeviceNotFoundError(StorageSetupError):
    def __init__(self, device: str):
        super().__init__(
            f"Target device '{device}' is not a valid block device on this system.",
            error_code="E_INVALID_DEVICE",
            remediation="Check available block devices using './drive_setup.py --scan --json'.",
        )


class MultiPartitionDiskError(StorageSetupError):
    def __init__(self, device: str, partitions: List[str]):
        super().__init__(
            f"Target '{device}' is a disk containing multiple partitions.",
            error_code="E_MULTIPART_DISK",
            remediation=f"Specify target partition explicitly with '-d {partitions[0]}'.",
            extra={"partitions": partitions},
        )


# ==============================================================================
# USER RESOLUTION
# ==============================================================================
def get_default_user() -> str:
    """Get invoking user if run under sudo, else system user via getpass."""
    return os.environ.get("SUDO_USER") or getpass.getuser()


# ==============================================================================
# CENTRALIZED SUBPROCESS RUNNER (WITH COMMAND & OUTPUT LOGGING)
# ==============================================================================
def run_cmd(
    cmd: List[str],
    check: bool = False,
    capture_output: bool = True,
    console: Optional[Console] = None,
) -> subprocess.CompletedProcess:
    """
    Executes a subprocess command, logging the command line, stdout, stderr,
    and exit status. Provides actionable sudo guidance if permission denied.
    """
    logger.debug(f"Executing: {' '.join(cmd)}")
    if capture_output:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.stdout and res.stdout.strip():
            logger.debug(f"Output:\n{res.stdout.strip()}")
        if res.stderr and res.stderr.strip():
            logger.debug(f"Stderr:\n{res.stderr.strip()}")
            if os.geteuid() != 0 and any(p in res.stderr.lower() for p in ("permission denied", "operation not permitted", "must be root")):
                logger.warning(f"Command '{cmd[0]}' requires administrative privileges. Run with sudo: sudo {' '.join(sys.argv)}")
        logger.debug(f"Exit code: {res.returncode}")
        if check and res.returncode != 0:
            raise subprocess.CalledProcessError(res.returncode, cmd, res.stdout, res.stderr)
        return res
    else:
        res = subprocess.run(cmd)
        logger.debug(f"Exit code: {res.returncode}")
        if res.returncode != 0 and os.geteuid() != 0:
            con = console or Console(stream=sys.stderr)
            con.print_error(f"\n{con.red(con.bold('[PERMISSION ERROR]'))} Command failed. If administrative privileges are needed, re-run with {con.bold('sudo')}:")
            con.print_error(f"  {con.bold('sudo ' + ' '.join(sys.argv))}\n")
        if check and res.returncode != 0:
            raise subprocess.CalledProcessError(res.returncode, cmd)
        return res


# ==============================================================================
# TELEMETRY & EVENT BREADCRUMBS GATEWAY
# ==============================================================================
class BreadcrumbPublisher:
    """
    Independent event telemetry gateway for shipping structured operational
    breadcrumbs to Grafana Alloy (loki.source.api) / Loki.
    Decoupled from storage logic; fail-safe and non-blocking.
    """

    def __init__(
        self,
        source: str,
        endpoint_url: Optional[str] = None,
        timeout: float = 1.0,
        enabled: bool = True,
    ):
        self.source = source
        self.endpoint_url = endpoint_url
        self.timeout = timeout
        self.enabled = enabled and bool(self.endpoint_url)
        try:
            self._hostname = socket.gethostname()
        except Exception:
            self._hostname = "localhost"

    def publish(
        self,
        action: str,
        payload: Dict[str, Any],
        device: Optional[str] = None,
    ) -> bool:
        """
        Accepts a structured payload, enriches it with contextual metadata,
        and pushes it to the configured Alloy/Loki HTTP endpoint.
        """
        if not self.enabled:
            return False

        now_ns = str(time.time_ns())
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 1. Automatic Metadata Enrichment
        operator = os.getenv("SUDO_USER") or getpass.getuser()
        enriched_data = {
            "source": self.source,
            "action": action,
            "device": device,
            "timestamp": now_iso,
            "host": self._hostname,
            "operator": operator,
            **payload,
        }

        # 2. Loki Push Schema Assembly
        stream_labels = {
            "source": self.source,
            "action": action,
        }
        if device:
            stream_labels["device"] = device.replace("/dev/", "")

        body = {
            "streams": [
                {
                    "stream": stream_labels,
                    "values": [
                        [now_ns, json.dumps(enriched_data)]
                    ],
                }
            ]
        }

        # 3. Fail-Safe HTTP Transport
        target_url = f"{self.endpoint_url.rstrip('/')}/loki/api/v1/push"
        try:
            req = urllib.request.Request(
                target_url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                logger.debug(f"Telemetry breadcrumb sent to {target_url} (HTTP {resp.status})")
                return resp.status in (200, 204)
        except Exception as err:
            logger.debug(f"Telemetry publish to {target_url} skipped/failed: {err}")
            return False


# ==============================================================================
# DATA MODELS
# ==============================================================================
@dataclass
class BlockDevice:
    name: str
    path: str
    size: str
    type: str
    fstype: Optional[str] = None
    label: Optional[str] = None
    uuid: Optional[str] = None
    mountpoints: List[str] = field(default_factory=list)
    model: Optional[str] = None
    rotational: Optional[bool] = None
    children: List["BlockDevice"] = field(default_factory=list)


@dataclass
class FstabEntry:
    spec: str
    mountpoint: str
    vfstype: str
    mntops: str
    freq: int
    passno: int
    raw_line: str


@dataclass
class DeviceState:
    device_path: str
    device: Optional[BlockDevice] = None
    parent_device: Optional[BlockDevice] = None
    child_partitions: List[BlockDevice] = field(default_factory=list)
    resolved_from_parent: bool = False
    original_input_path: str = ""
    is_block_device: bool = False
    is_formatted: bool = False
    fstype: Optional[str] = None
    uuid: Optional[str] = None
    label: Optional[str] = None
    is_mounted: bool = False
    current_mounts: List[str] = field(default_factory=list)
    in_fstab: bool = False
    fstab_entry: Optional[FstabEntry] = None
    mountpoint_exists: bool = False
    mountpoint_is_mounted: bool = False
    mountpoint_active_src: Optional[str] = None
    mountpoint_owner: Optional[str] = None
    mountpoint_perms: Optional[str] = None
    # Storage efficiency metrics (populated via tune2fs for ext4)
    total_inodes: Optional[int] = None
    free_inodes: Optional[int] = None
    used_inodes: Optional[int] = None
    inode_size: Optional[int] = None
    inode_table_overhead_bytes: Optional[int] = None
    reserved_block_count: Optional[int] = None
    block_size: Optional[int] = None
    total_blocks: Optional[int] = None
    reserved_space_bytes: Optional[int] = None
    reserved_percent: Optional[float] = None
    detected_inode_profile: Optional[str] = None
    # Hardware & Power Management (APM)
    is_rotational: Optional[bool] = None
    apm_supported: Optional[bool] = None
    apm_level: Optional[int] = None
    apm_status_label: Optional[str] = None
    apm_thermal_note: Optional[str] = None


@dataclass
class StepDecision:
    step_id: int
    name: str
    action: str  # "EXECUTE", "SKIP", "FORCE_OVERRIDE"
    reason: str
    sub_text: Optional[str] = None


@dataclass
class UnconfiguredDevice:
    device: BlockDevice
    state: DeviceState
    status: str
    status_badge: str
    details: str


@dataclass
class ScanReport:
    configured_devices: List[Tuple[BlockDevice, DeviceState]]
    unconfigured_devices: List[UnconfiguredDevice]
    unconfigured_only: bool = False


@dataclass
class VerifyReport:
    state: DeviceState
    target_mountpoint: Optional[str]
    target_user: str
    all_healthy: bool
    missing_steps: List[str]


@dataclass
class ActionPlan:
    device: str
    target_mountpoint: Optional[str]
    target_user: str
    fstype: str
    label: Optional[str]
    inode_reserve_type: str
    reserved_percent: int
    force: bool
    decisions: List[StepDecision]
    model: str = "Generic"
    size: str = "Unknown"
    execute_count: int = 0
    skip_count: int = 0


# ==============================================================================
# ERROR PRESENTER (STRATEGY PATTERN)
# ==============================================================================
class ErrorPresenter:
    """Strategy for rendering errors in JSON or human-readable format."""

    @staticmethod
    def render_error(
        error_code: str,
        message: str,
        remediation: str,
        console: Optional[Console] = None,
        json_mode: bool = False,
        extra: Optional[Dict] = None,
    ) -> None:
        if json_mode:
            payload = {
                "status": "error",
                "error_code": error_code,
                "message": message,
                "remediation": remediation,
            }
            if extra:
                payload.update(extra)
            print(json.dumps(payload, indent=2), flush=True)
        else:
            con = console or Console(stream=sys.stderr)
            con.print_error(f"\n{con.red(con.bold('[ERROR]'))} {message}")
            if extra and "partitions" in extra:
                con.print_error("  Partitions detected:")
                for p in extra["partitions"]:
                    con.print_error(f"  → {p}")
            if remediation:
                con.print_error(f"        {con.bold('Remediation:')} {remediation}\n")


# ==============================================================================
# USER CONFIRMATION HELPER (FAIL-FAST NON-INTERACTIVE GUARDRAIL)
# ==============================================================================
def confirm(
    explanation: str,
    action_message: str,
    cmd: Optional[Union[List[str], str]] = None,
    assume_yes: bool = False,
    console: Optional[Console] = None,
    json_mode: bool = False,
    step_name: str = "",
) -> bool:
    con = console or Console(stream=sys.stdout)

    if not json_mode:
        con.print("\n" + "-" * 50)
        con.print(f"{con.bold('UPCOMING ACTION:')} {action_message}")
        if cmd is not None:
            if isinstance(cmd, list):
                cmd_str = " ".join(cmd)
                con.print(f"{con.cyan(con.bold('Command:'))}         {con.bold(cmd_str)}")
                con.print(f"{con.cyan('Exec List:')}       {cmd}")
            else:
                con.print(f"{con.cyan(con.bold('Command:'))}         {con.bold(cmd)}")
        con.print(f"{con.yellow('Explanation:')}     {explanation}")
        con.print("-" * 50)

    if assume_yes:
        if not json_mode:
            con.print(f"{con.green('[INFO]')} --yes flag detected. Proceeding automatically...")
        return True

    if not sys.stdin.isatty():
        logger.error("Non-interactive terminal detected without '-y' / '--yes'. Cannot prompt for confirmation.")
        ErrorPresenter.render_error(
            error_code="E_NON_INTERACTIVE",
            message="Operation requires confirmation, but environment is non-interactive.",
            remediation="Pass '-y' or '--yes' to proceed non-interactively.",
            console=con,
            json_mode=json_mode,
            extra={"step": step_name} if step_name else None,
        )
        sys.exit(2)

    try:
        response = input("Do you want to proceed with this step? (y/N): ").strip().lower()
        if response in ("y", "yes"):
            return True
        con.print(f"{con.red('[SKIPPED]')} User cancelled the action.")
        return False
    except (KeyboardInterrupt, EOFError):
        con.print_error(f"\n{con.red('[ABORTED]')} Operation interrupted by user.")
        sys.exit(1)


# ==============================================================================
# FSTAB MANAGER (SAFE & ATOMIC)
# ==============================================================================
class FstabManager:
    FSTAB_PATH = Path("/etc/fstab")

    @classmethod
    def read_entries(cls) -> List[FstabEntry]:
        entries: List[FstabEntry] = []
        if not cls.FSTAB_PATH.exists():
            return entries

        with open(cls.FSTAB_PATH, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split()
                if len(parts) >= 6:
                    entries.append(
                        FstabEntry(
                            spec=parts[0],
                            mountpoint=parts[1],
                            vfstype=parts[2],
                            mntops=parts[3],
                            freq=int(parts[4]),
                            passno=int(parts[5]),
                            raw_line=line,
                        )
                    )
                elif len(parts) >= 4:
                    entries.append(
                        FstabEntry(
                            spec=parts[0],
                            mountpoint=parts[1],
                            vfstype=parts[2],
                            mntops=parts[3],
                            freq=0,
                            passno=2,
                            raw_line=line,
                        )
                    )
        return entries

    @classmethod
    def find_entry(cls, uuid: Optional[str], device_path: Optional[str], mountpoint: Optional[str]) -> Optional[FstabEntry]:
        entries = cls.read_entries()
        for e in entries:
            if uuid and (f"UUID={uuid}" in e.spec or f'UUID="{uuid}"' in e.spec):
                return e
            if device_path and (e.spec == device_path or os.path.realpath(e.spec) == os.path.realpath(device_path)):
                return e
            if mountpoint and e.mountpoint == mountpoint:
                return e
        return None

    @classmethod
    def append_entry(cls, uuid: str, mountpoint: str, fstype: str = "ext4", options: str = "defaults,nofail", freq: int = 0, passno: int = 2) -> None:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_path = cls.FSTAB_PATH.with_name(f"fstab.bak.{timestamp}")
        tmp_path = cls.FSTAB_PATH.with_name(f"fstab.tmp.{os.getpid()}")

        with open(cls.FSTAB_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        shutil.copy2(cls.FSTAB_PATH, backup_path)
        logger.info(f"Created /etc/fstab backup at: {backup_path}")

        new_entry = (
            f"\n# Storage Mount point for {mountpoint}\n"
            f"UUID={uuid}  {mountpoint}  {fstype}  {options}  {freq}  {passno}\n"
        )

        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.write(new_entry)
            f.flush()
            os.fsync(f.fileno())

        shutil.copymode(cls.FSTAB_PATH, tmp_path)
        os.replace(tmp_path, cls.FSTAB_PATH)
        logger.info(f"Successfully configured /etc/fstab with UUID={uuid}.")


# ==============================================================================
# UNIFIED DEVICE INSPECTION ENGINE
# ==============================================================================
class DeviceInspector:
    @staticmethod
    def _parse_block_device_node(data: dict) -> BlockDevice:
        mounts = data.get("mountpoints") or []
        if isinstance(mounts, str):
            mounts = [mounts] if mounts else []
        elif not isinstance(mounts, list):
            mounts = []

        mounts = [m for m in mounts if m]

        rota_raw = data.get("rota")
        rotational: Optional[bool] = None
        if rota_raw is not None:
            if isinstance(rota_raw, bool):
                rotational = rota_raw
            elif isinstance(rota_raw, (int, str)):
                rotational = str(rota_raw).strip().lower() in ("1", "true")

        dev = BlockDevice(
            name=data.get("name", ""),
            path=data.get("path") or f"/dev/{data.get('name', '')}",
            size=data.get("size") or "",
            type=data.get("type") or "",
            fstype=data.get("fstype") or None,
            label=data.get("label") or None,
            uuid=data.get("uuid") or None,
            mountpoints=mounts,
            model=(data.get("model") or "").strip() or None,
            rotational=rotational,
        )

        for child in data.get("children", []):
            dev.children.append(DeviceInspector._parse_block_device_node(child))

        return dev

    @classmethod
    def get_all_block_devices(cls) -> List[BlockDevice]:
        cmd = ["lsblk", "-J", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,ROTA"]
        try:
            res = run_cmd(cmd, check=True)
            parsed = json.loads(res.stdout)
            raw_list = parsed.get("blockdevices", [])
            return [cls._parse_block_device_node(item) for item in raw_list]
        except Exception as err:
            logger.error(f"Failed to query block devices via lsblk: {err}")
            return []

    @classmethod
    def is_device_rotational(cls, dev_name_or_path: str) -> bool:
        """
        Check if device is a rotational HDD (1) vs SSD/NVMe (0).
        Inspects /sys/block/<disk>/queue/rotational first, falls back to lsblk.
        """
        clean_name = os.path.basename(os.path.realpath(dev_name_or_path))
        disk_name = clean_name
        if disk_name.startswith("nvme") or disk_name.startswith("mmcblk"):
            if "p" in disk_name:
                disk_name = disk_name.rsplit("p", 1)[0]
        else:
            disk_name = disk_name.rstrip("0123456789")

        sysfs_rota = Path(f"/sys/block/{disk_name}/queue/rotational")
        if sysfs_rota.exists():
            try:
                return sysfs_rota.read_text().strip() == "1"
            except Exception:
                pass
        return True

    @classmethod
    def get_apm_info(cls, disk_path: str, is_rotational: bool) -> Tuple[Optional[bool], Optional[int], str, str]:
        """
        Queries ATA Advanced Power Management (APM) via hdparm on rotational disks.
        Returns: (apm_supported, apm_level, status_label, thermal_note)
        """
        if not is_rotational:
            return (
                False,
                None,
                "N/A (Solid State Drive)",
                "Solid State Drives (SSDs/NVMe) do not use ATA APM.",
            )

        if not shutil.which("hdparm"):
            return (
                None,
                None,
                "N/A (hdparm utility not installed)",
                "Install hdparm to inspect and tune ATA Power Management (sudo apt install hdparm).",
            )

        try:
            res = run_cmd(["hdparm", "-B", disk_path], check=False)
        except Exception as err:
            return (
                None,
                None,
                f"Error querying APM: {err}",
                "Unable to execute hdparm command.",
            )

        out = (res.stdout or "") + (res.stderr or "")

        if "Permission denied" in out or "bad/missing sense data" in out or (res.returncode != 0 and os.geteuid() != 0):
            return (
                None,
                None,
                "Requires root (sudo) to inspect via hdparm",
                "Run with 'sudo' to inspect low-level ATA APM registers.",
            )

        if "not supported" in out.lower():
            return (
                False,
                None,
                "Not supported by device / enclosure",
                "Drive firmware or USB bridge controller does not support ATA APM.",
            )

        m = re.search(r"APM_level\s*=\s*(\w+)", out, re.IGNORECASE)
        if m:
            val_str = m.group(1).lower()
            if val_str == "off":
                return (
                    True,
                    255,
                    "Disabled (off)",
                    "APM is disabled. Heads remain loaded; maximum performance with higher idle power.",
                )
            try:
                val = int(val_str)
                if val == 254:
                    label = "Level 254 (Max Performance / Heads Loaded)"
                    note = "Head parking disabled (0 wake latency / no cycle wear). Notice: Higher thermals (~+5°C to +8°C)."
                elif 128 <= val <= 253:
                    label = f"Level {val} (Standard Idle)"
                    note = f"Spindown disabled, idle head parking active (cooler operation). Trade-off: APM 254 eliminates head parking but increases drive temps by 5°C-8°C."
                elif 1 <= val <= 127:
                    label = f"Level {val} (Power Saving - Spindown Enabled)"
                    note = "Aggressive power saving: spindle spindown permitted. Results in wake latency and spindle restart wear."
                else:
                    label = f"Level {val}"
                    note = f"Current APM level: {val}."
                return (True, val, label, note)
            except ValueError:
                return (True, None, f"Level {val_str}", f"APM reported value: {val_str}")

        return (None, None, "Unknown APM status", "Could not parse APM output from hdparm.")

    @classmethod
    def find_device_and_parent(cls, target_path: str) -> Tuple[Optional[BlockDevice], Optional[BlockDevice]]:
        target_real = os.path.realpath(target_path)
        all_devs = cls.get_all_block_devices()

        for root_dev in all_devs:
            if os.path.realpath(root_dev.path) == target_real or root_dev.path == target_path:
                return root_dev, None
            for child in root_dev.children:
                if os.path.realpath(child.path) == target_real or child.path == target_path:
                    return child, root_dev
        return None, None

    @classmethod
    def resolve_target(cls, input_path: str, target_mountpoint: Optional[str] = None) -> Tuple[str, Optional[BlockDevice], Optional[BlockDevice], List[BlockDevice], bool]:
        """
        Resolves input device path:
        - If input is a partition (e.g. /dev/sdb1), returns it and its parent disk.
        - If input is a disk with 1 partition (e.g. /dev/sdb with /dev/sdb1), auto-resolves to /dev/sdb1.
        - If input is a disk with multiple partitions, checks if one matches target_mountpoint.
        Returns: (effective_path, target_dev, parent_dev, child_partitions, resolved_from_parent)
        """
        logger.info("Probing block device topology via lsblk...")
        target_dev, parent_dev = cls.find_device_and_parent(input_path)

        if not target_dev:
            return input_path, None, None, [], False

        # If it's a partition directly
        if target_dev.type == "part":
            return input_path, target_dev, parent_dev, [], False

        # If it's a disk with partitions
        if target_dev.type == "disk" and len(target_dev.children) > 0:
            # Case 1: Exactly one partition
            if len(target_dev.children) == 1:
                child = target_dev.children[0]
                logger.info(f"Target '{input_path}' is a disk containing single partition '{child.path}'. Automatically targeting '{child.path}'...")
                return child.path, child, target_dev, target_dev.children, True

            # Case 2: Multiple partitions
            if target_mountpoint:
                for child in target_dev.children:
                    if target_mountpoint in child.mountpoints:
                        logger.info(f"Target '{input_path}' has multiple partitions. Matched partition '{child.path}' active at '{target_mountpoint}'. Targeting '{child.path}'...")
                        return child.path, child, target_dev, target_dev.children, True

            return input_path, target_dev, None, target_dev.children, False

        # Raw disk with no partitions
        return input_path, target_dev, None, [], False

    @classmethod
    def inspect(cls, device_path: str, target_mountpoint: Optional[str] = None, target_user: Optional[str] = None) -> DeviceState:
        eff_path, dev, parent, children, resolved = cls.resolve_target(device_path, target_mountpoint)

        state = DeviceState(
            device_path=eff_path,
            original_input_path=device_path,
            device=dev,
            parent_device=parent,
            child_partitions=children,
            resolved_from_parent=resolved,
        )

        dev_path_obj = Path(eff_path)
        if not dev_path_obj.exists() or not dev_path_obj.is_block_device():
            state.is_block_device = False
            return state

        state.is_block_device = True

        # Query attributes directly via blkid
        logger.info(f"Querying filesystem and UUID attributes via blkid for {eff_path}...")
        try:
            blkid_cmd = ["blkid", "-o", "export", eff_path]
            blkid_out = run_cmd(blkid_cmd)
            if blkid_out.returncode == 0:
                attrs: Dict[str, str] = {}
                for line in blkid_out.stdout.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        attrs[k.strip()] = v.strip()
                state.fstype = attrs.get("TYPE")
                state.uuid = attrs.get("UUID")
                state.label = attrs.get("LABEL")
                state.is_formatted = bool(state.fstype)
        except Exception:
            pass

        # Fallback to lsblk attributes if blkid didn't populate
        if state.device and not state.is_formatted and state.device.fstype:
            state.fstype = state.device.fstype
            state.uuid = state.device.uuid
            state.label = state.device.label
            state.is_formatted = True

        # Current mount inspection via /proc/mounts
        logger.info("Inspecting active mount table in /proc/mounts...")
        try:
            real_eff = os.path.realpath(eff_path)
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        src, tgt = parts[0], parts[1]
                        if src == eff_path or os.path.realpath(src) == real_eff:
                            state.current_mounts.append(tgt)
            state.is_mounted = len(state.current_mounts) > 0
        except Exception:
            pass

        # /etc/fstab entry check
        logger.info("Parsing /etc/fstab for persistent entries...")
        effective_mp = target_mountpoint or (state.current_mounts[0] if state.current_mounts else None)
        state.fstab_entry = FstabManager.find_entry(uuid=state.uuid, device_path=eff_path, mountpoint=effective_mp)
        state.in_fstab = state.fstab_entry is not None

        # Mountpoint directory inspection
        if effective_mp:
            mp_path = Path(effective_mp)
            state.mountpoint_exists = mp_path.exists() and mp_path.is_dir()
            if state.mountpoint_exists:
                try:
                    findmnt = run_cmd(["findmnt", "-no", "SOURCE", "-T", effective_mp])
                    if findmnt.returncode == 0 and findmnt.stdout.strip():
                        state.mountpoint_is_mounted = True
                        state.mountpoint_active_src = findmnt.stdout.strip()
                except Exception:
                    pass

                try:
                    st = mp_path.stat()
                    owner_name = pwd.getpwuid(st.st_uid).pw_name
                    perms_octal = oct(st.st_mode)[-3:]
                    state.mountpoint_owner = owner_name
                    state.mountpoint_perms = perms_octal
                except Exception:
                    pass

        # Query ext4 storage efficiency metadata via tune2fs (requires root)
        if state.fstype == "ext4" and os.geteuid() == 0:
            try:
                tune_out = run_cmd(["tune2fs", "-l", eff_path])
                if tune_out.returncode == 0:
                    tune_data: Dict[str, str] = {}
                    for line in tune_out.stdout.splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            tune_data[k.strip()] = v.strip()

                    def _parse_int(val: Optional[str]) -> Optional[int]:
                        if val is None:
                            return None
                        try:
                            return int(val)
                        except ValueError:
                            return None

                    total_inodes = _parse_int(tune_data.get("Inode count"))
                    free_inodes = _parse_int(tune_data.get("Free inodes"))
                    inode_size = _parse_int(tune_data.get("Inode size"))
                    block_size = _parse_int(tune_data.get("Block size"))
                    total_blocks = _parse_int(tune_data.get("Block count"))
                    reserved_blocks = _parse_int(tune_data.get("Reserved block count"))

                    state.total_inodes = total_inodes
                    state.free_inodes = free_inodes
                    if total_inodes is not None and free_inodes is not None:
                        state.used_inodes = total_inodes - free_inodes
                    state.inode_size = inode_size

                    if total_inodes is not None and inode_size is not None:
                        state.inode_table_overhead_bytes = total_inodes * inode_size

                    state.block_size = block_size
                    state.total_blocks = total_blocks
                    state.reserved_block_count = reserved_blocks

                    if reserved_blocks is not None and block_size is not None:
                        state.reserved_space_bytes = reserved_blocks * block_size

                    if reserved_blocks is not None and total_blocks is not None and total_blocks > 0:
                        state.reserved_percent = round((reserved_blocks / total_blocks) * 100, 2)

                    if total_inodes is not None and total_blocks is not None and block_size is not None and total_inodes > 0:
                        bytes_per_inode = (total_blocks * block_size) // total_inodes
                        if bytes_per_inode > 2 * 1024 * 1024:
                            state.detected_inode_profile = "largefile4"
                        elif bytes_per_inode >= 512 * 1024:
                            state.detected_inode_profile = "largefile"
                        else:
                            state.detected_inode_profile = "default"
            except Exception:
                pass

        # Query hardware / rotational topology and APM
        parent_disk_path = state.parent_device.path if state.parent_device else state.device_path
        state.is_rotational = cls.is_device_rotational(parent_disk_path)
        apm_supp, apm_lvl, apm_lbl, apm_note = cls.get_apm_info(parent_disk_path, state.is_rotational)
        state.apm_supported = apm_supp
        state.apm_level = apm_lvl
        state.apm_status_label = apm_lbl
        state.apm_thermal_note = apm_note

        return state


# ==============================================================================
# AUDIT MODE (--verify) & PRESENTER
# ==============================================================================
def do_verify(
    device_path: str,
    target_mountpoint: Optional[str] = None,
    target_user: Optional[str] = None,
) -> VerifyReport:
    """Pure inspection logic for verifying storage device state against target expectations."""
    target_user = target_user or get_default_user()
    state = DeviceInspector.inspect(device_path, target_mountpoint, target_user)

    if not state.is_block_device:
        raise DeviceNotFoundError(device_path)

    if state.device and state.device.type == "disk" and len(state.child_partitions) > 1 and not state.resolved_from_parent:
        raise MultiPartitionDiskError(device_path, [p.path for p in state.child_partitions])

    all_healthy = True
    missing_steps: List[str] = []

    # 1. Filesystem (--format / -l)
    if not state.is_formatted:
        all_healthy = False
        missing_steps.append("--format -l <label> (format drive as ext4)")

    # 2. Mount status (--mount / -m)
    if target_mountpoint:
        if target_mountpoint in state.current_mounts:
            pass
        elif state.is_mounted:
            all_healthy = False
            missing_steps.append(f"--mount (remount to {target_mountpoint})")
        else:
            all_healthy = False
            missing_steps.append(f"--mount (mount device to {target_mountpoint})")

    # 3. Persistence (--fstab)
    if state.in_fstab and state.fstab_entry:
        if target_mountpoint and state.fstab_entry.mountpoint != target_mountpoint:
            all_healthy = False
            missing_steps.append(f"--fstab (update /etc/fstab entry to {target_mountpoint})")
    else:
        all_healthy = False
        missing_steps.append("--fstab (add persistent mount entry)")

    # 4. Target Directory Permissions (--perms / -u)
    if target_mountpoint:
        if state.mountpoint_exists:
            if state.mountpoint_owner != target_user:
                missing_steps.append(f"--perms (set ownership to {target_user})")
        else:
            missing_steps.append(f"--perms (create directory and set ownership)")

    return VerifyReport(
        state=state,
        target_mountpoint=target_mountpoint,
        target_user=target_user,
        all_healthy=(all_healthy and len(missing_steps) == 0),
        missing_steps=missing_steps,
    )


class VerifyPresenter:
    """Strategy for rendering verification reports in JSON or human-readable table."""

    @staticmethod
    def render_table(report: VerifyReport, console: Console) -> int:
        state = report.state
        target_mountpoint = report.target_mountpoint
        target_user = report.target_user

        dev_model = "Generic"
        if state.device and state.device.model:
            dev_model = state.device.model
        elif state.parent_device and state.parent_device.model:
            dev_model = state.parent_device.model

        dev_size = state.device.size if state.device and state.device.size else "Unknown"
        dev_type = state.device.type if state.device and state.device.type else "block"

        console.print(f"\n{console.bold('=' * 70)}")
        console.print(f"                {console.cyan(console.bold('STORAGE DEVICE AUDIT: ' + state.device_path))}")
        console.print(f"{console.bold('=' * 70)}")

        if os.geteuid() != 0:
            console.print(f"{console.yellow('[NOTICE]')} Audit running without root privileges (sudo).")
            console.print("         Ext4 geometry metrics (tune2fs) and raw device attributes may be restricted.")
            console.print(f"         For complete audit details, run with: {console.bold('sudo ' + ' '.join(sys.argv))}\n")

        if state.resolved_from_parent and state.parent_device:
            dev_header = f"{console.bold(state.device_path)} (Parent: {state.parent_device.path}, {dev_model}, {dev_size}, {dev_type})"
        else:
            dev_header = f"{console.bold(state.device_path)} ({dev_model}, {dev_size}, {dev_type})"

        console.print(f"Target Device:     {dev_header}")
        console.print(f"Target Mountpoint: {console.bold(target_mountpoint or '[None Specified]')}")
        console.print(f"Target Owner:      {console.bold(target_user)}")
        console.print("-" * 70)
        console.print(f"{console.bold('COMPONENT AUDIT:')}")

        # 1. Filesystem (--format / -l)
        if state.is_formatted:
            console.print(f"  [{console.green('✓')}] Filesystem (--format):       {console.bold(state.fstype or 'unknown')} | UUID: {state.uuid or 'none'} | Label (-l): \"{state.label or 'none'}\"")
        else:
            console.print(f"  [{console.red('✗')}] Filesystem (--format):       {console.red('No recognizable filesystem detected (unformatted)')}")

        # 2. Mount status (--mount / -m)
        if target_mountpoint:
            if target_mountpoint in state.current_mounts:
                console.print(f"  [{console.green('✓')}] Active Mount (--mount):      Mounted at {console.bold(target_mountpoint)} (-m)")
            elif state.is_mounted:
                other_mount = state.current_mounts[0]
                other_msg = f"Mounted at '{other_mount}', expected '{target_mountpoint}'"
                console.print(f"  [{console.yellow('!')}] Active Mount (--mount):      {console.yellow(other_msg)}")
            else:
                console.print(f"  [{console.red('✗')}] Active Mount (--mount):      {console.red('Device is currently unmounted')}")
        else:
            if state.is_mounted:
                console.print(f"  [{console.cyan('ℹ')}] Active Mount (--mount):      Currently mounted at: {', '.join(state.current_mounts)}")
            else:
                console.print(f"  [{console.cyan('ℹ')}] Active Mount (--mount):      Not mounted")

        # 3. Persistence (--fstab)
        if state.in_fstab and state.fstab_entry:
            if target_mountpoint:
                if state.fstab_entry.mountpoint == target_mountpoint:
                    console.print(f"  [{console.green('✓')}] Persistence (--fstab):       Valid persistent entry found in /etc/fstab for {console.bold(target_mountpoint)}")
                else:
                    fstab_msg = f"/etc/fstab entry points to '{state.fstab_entry.mountpoint}' instead of '{target_mountpoint}'"
                    console.print(f"  [{console.yellow('!')}] Persistence (--fstab):       {console.yellow(fstab_msg)}")
            else:
                console.print(f"  [{console.green('✓')}] Persistence (--fstab):       Found in /etc/fstab: {state.fstab_entry.spec} -> {state.fstab_entry.mountpoint}")
        else:
            console.print(f"  [{console.red('✗')}] Persistence (--fstab):       {console.red('No persistent entry found in /etc/fstab for this device/UUID')}")

        # 4. Target Directory Permissions (--perms / -u)
        if target_mountpoint:
            if state.mountpoint_exists:
                if state.mountpoint_owner == target_user:
                    console.print(f"  [{console.green('✓')}] Permissions (--perms):       Directory exists | Owner (-u): {state.mountpoint_owner} | Perms: {state.mountpoint_perms}")
                else:
                    perm_msg = f"Directory exists | Owner: {state.mountpoint_owner} (expected -u: {target_user}) | Perms: {state.mountpoint_perms}"
                    console.print(f"  [{console.yellow('!')}] Permissions (--perms):       {console.yellow(perm_msg)}")
            else:
                missing_dir_msg = f"Directory '{target_mountpoint}' does not exist yet"
                console.print(f"  [{console.red('✗')}] Permissions (--perms):       {console.red(missing_dir_msg)}")

        if state.fstype == "ext4":
            console.print("-" * 70)
            console.print(f"{console.bold('STORAGE EFFICIENCY & ALLOCATION:')}")

            if state.total_inodes is not None:
                overhead_mb = (state.inode_table_overhead_bytes or 0) / (1024 * 1024)
                if overhead_mb >= 1024:
                    overhead_str = f"{overhead_mb / 1024:.2f} GB"
                else:
                    overhead_str = f"{overhead_mb:.1f} MB"

                used_inodes_str = f"{state.used_inodes:,}" if state.used_inodes is not None else "unknown"
                total_inodes_str = f"{state.total_inodes:,}" if state.total_inodes is not None else "unknown"
                free_inodes_str = f"{state.free_inodes:,}" if state.free_inodes is not None else "unknown"

                profile_str = f" [Profile: {state.detected_inode_profile}]" if state.detected_inode_profile else ""
                console.print(f"  Inodes (-irt):                   {total_inodes_str} total ({used_inodes_str} used, {free_inodes_str} free){profile_str} | Table Overhead: {overhead_str}")

                res_bytes = state.reserved_space_bytes or 0
                res_gb = res_bytes / (1024 * 1024 * 1024)
                res_pct = state.reserved_percent if state.reserved_percent is not None else 0.0

                console.print(f"  Reserved Space (-trb / -r):      {res_pct:.1f}% ({res_gb:.1f} GB reserved for root) [Tool Target: 1%]")

                if res_pct >= 3.0:
                    target_1pct_gb = (res_bytes / (res_pct / 100)) * 0.01 / (1024 * 1024 * 1024) if res_pct > 0 else 0
                    reclaim_gb = res_gb - target_1pct_gb
                    console.print(f"  {console.yellow('[!]')} Optimization Notice: {res_pct:.0f}% reserved space detected (~{res_gb:.1f} GB).")
                    console.print(f"      Reclaim ~{reclaim_gb:.1f} GB by tuning to 1%: {console.bold(f'sudo ./drive_setup.py -d {state.device_path} -trb -r 1')}")
            else:
                if os.geteuid() != 0:
                    console.print(f"  {console.yellow('[!]')} Ext4 allocation statistics could not be retrieved (permission denied).")
                    console.print(f"      To inspect inodes & reserved blocks via tune2fs, please run with {console.bold('sudo')}:")
                    console.print(f"      {console.bold('sudo ' + ' '.join(sys.argv))}")
                else:
                    console.print(f"  {console.yellow('[!]')} tune2fs could not read ext4 geometry for {state.device_path}.")

        # Hardware & Power Management (APM)
        console.print("-" * 70)
        console.print(f"{console.bold('HARDWARE & POWER MANAGEMENT (APM):')}")
        if state.is_rotational is False:
            console.print(f"  [{console.cyan('ℹ')}] ATA Power Mgmt (APM):         {console.dim('N/A (Solid State Drive)')}")
        elif state.apm_level is not None:
            if state.apm_level == 254:
                badge = console.green("ℹ")
            elif 128 <= state.apm_level <= 253:
                badge = console.cyan("ℹ")
            else:
                badge = console.yellow("ℹ")
            console.print(f"  [{badge}] ATA Power Mgmt (APM):         {console.bold(state.apm_status_label or f'Level {state.apm_level}')}")
            if state.apm_thermal_note:
                console.print(f"      {console.dim(state.apm_thermal_note)}")
        else:
            status_text = state.apm_status_label or "Not supported or unavailable"
            console.print(f"  [{console.cyan('ℹ')}] ATA Power Mgmt (APM):         {status_text}")
            if state.apm_thermal_note:
                console.print(f"      {console.dim(state.apm_thermal_note)}")

        console.print("-" * 70)
        if report.all_healthy:
            console.print(f"{console.green(console.bold('✓ AUDIT RESULT: Storage setup is complete, healthy, and persistent!'))}")
            console.print("No setup actions required.")
            console.print(f"{console.bold('=' * 70)}\n")
            return 0
        else:
            console.print(f"{console.yellow(console.bold('⚠ AUDIT RESULT: Storage setup is incomplete or needs adjustment.'))}")
            console.print("Recommended actions:")
            for step in report.missing_steps:
                console.print(f"  {console.bold('→')} {step}")
            cmd_hint = f"sudo ./drive_setup.py -d {state.device_path} -m {target_mountpoint or '/mnt/<dir>'} --all"
            console.print(f"\nRun suggested actions via: {console.bold(cmd_hint)}")
            console.print(f"{console.bold('=' * 70)}\n")
            return 1

    @staticmethod
    def render_json(report: VerifyReport) -> int:
        state = report.state
        dev_model = "Generic"
        if state.device and state.device.model:
            dev_model = state.device.model
        elif state.parent_device and state.parent_device.model:
            dev_model = state.parent_device.model
        dev_size = state.device.size if state.device and state.device.size else "Unknown"
        dev_type = state.device.type if state.device and state.device.type else "block"

        payload = {
            "status": "success",
            "mode": "verify",
            "device": state.device_path,
            "original_input_path": state.original_input_path,
            "resolved_from_parent": state.resolved_from_parent,
            "parent_device": state.parent_device.path if state.parent_device else None,
            "model": dev_model,
            "size": dev_size,
            "type": dev_type,
            "target_mountpoint": report.target_mountpoint,
            "target_user": report.target_user,
            "components": {
                "filesystem": {
                    "healthy": state.is_formatted,
                    "fstype": state.fstype,
                    "uuid": state.uuid,
                    "label": state.label,
                },
                "mount": {
                    "healthy": bool(report.target_mountpoint and report.target_mountpoint in state.current_mounts) if report.target_mountpoint else state.is_mounted,
                    "is_mounted": state.is_mounted,
                    "current_mounts": state.current_mounts,
                    "expected_mountpoint": report.target_mountpoint,
                },
                "persistence": {
                    "healthy": bool(state.in_fstab and (not report.target_mountpoint or (state.fstab_entry and state.fstab_entry.mountpoint == report.target_mountpoint))),
                    "in_fstab": state.in_fstab,
                    "fstab_entry": {
                        "spec": state.fstab_entry.spec,
                        "mountpoint": state.fstab_entry.mountpoint,
                        "fstype": state.fstab_entry.vfstype,
                        "options": state.fstab_entry.mntops,
                    } if state.fstab_entry else None,
                },
                "permissions": {
                    "healthy": bool(report.target_mountpoint and state.mountpoint_exists and state.mountpoint_owner == report.target_user),
                    "directory_exists": state.mountpoint_exists,
                    "owner": state.mountpoint_owner,
                    "expected_owner": report.target_user,
                    "perms": state.mountpoint_perms,
                } if report.target_mountpoint else None,
            },
            "efficiency": {
                "total_inodes": state.total_inodes,
                "used_inodes": state.used_inodes,
                "free_inodes": state.free_inodes,
                "inode_profile": state.detected_inode_profile,
                "inode_table_overhead_bytes": state.inode_table_overhead_bytes,
                "reserved_space_bytes": state.reserved_space_bytes,
                "reserved_percent": state.reserved_percent,
            } if state.fstype == "ext4" else None,
            "apm": {
                "is_rotational": state.is_rotational,
                "supported": state.apm_supported,
                "level": state.apm_level,
                "status": state.apm_status_label,
                "thermal_tradeoff_note": state.apm_thermal_note,
            },
            "all_healthy": report.all_healthy,
            "missing_steps": report.missing_steps,
            "recommended_command": f"sudo ./drive_setup.py -d {state.device_path} -m {report.target_mountpoint or '/mnt/storage'} --all" if report.missing_steps else None,
        }
        print(json.dumps(payload, indent=2), flush=True)
        return 0 if report.all_healthy else 1


# ==============================================================================
# DISCOVERY MODE (--scan) & PRESENTER
# ==============================================================================
def do_scan(unconfigured_only: bool = False) -> ScanReport:
    """Pure inspection logic for discovering configured and unconfigured storage devices."""
    logger.info("Scanning system block devices via lsblk...")
    all_devs = DeviceInspector.get_all_block_devices()
    if not all_devs:
        raise StorageSetupError(
            "No block devices found or lsblk is unavailable.",
            error_code="E_NO_BLOCK_DEVICES",
            remediation="Ensure lsblk is installed and accessible.",
        )

    candidates: List[BlockDevice] = []

    def is_system_device(dev: BlockDevice) -> bool:
        for mp in dev.mountpoints:
            if mp in ("/", "/boot", "/boot/efi", "/efi", "/home"):
                return True
        for child in dev.children:
            if is_system_device(child):
                return True
        return False

    def collect(dev: BlockDevice):
        if any(dev.name.startswith(pfx) for pfx in ("loop", "ram", "zram", "sr")):
            return
        if is_system_device(dev):
            return

        if dev.type == "disk" and len(dev.children) > 0:
            for child in dev.children:
                collect(child)
            return

        candidates.append(dev)

    for d in all_devs:
        collect(d)

    configured_devices: List[Tuple[BlockDevice, DeviceState]] = []
    unconfigured_devices: List[UnconfiguredDevice] = []

    for dev in candidates:
        state = DeviceInspector.inspect(dev.path)
        is_fully_configured = state.is_formatted and state.is_mounted and state.in_fstab

        if is_fully_configured:
            configured_devices.append((dev, state))
        else:
            status_badge = ""
            details = ""
            status_clean = "UNKNOWN"
            if not state.is_formatted:
                status_clean = "RAW_UNFORMATTED"
                status_badge = "[RAW / UNFORMATTED]"
                details = "Ready for full setup with --format"
            elif not state.is_mounted and not state.in_fstab:
                status_clean = "UNCONFIGURED_FS"
                status_badge = "[UNCONFIGURED FS]"
                details = "Formatted, unmounted, missing fstab"
            elif state.is_mounted and not state.in_fstab:
                status_clean = "TEMP_MOUNTED"
                status_badge = "[TEMP MOUNTED]"
                details = f"Mounted at '{state.current_mounts[0]}', missing fstab"
            elif not state.is_mounted and state.in_fstab:
                status_clean = "UNMOUNTED_IN_FSTAB"
                status_badge = "[UNMOUNTED IN FSTAB]"
                details = "Listed in fstab, but currently unmounted"

            unconfigured_devices.append(
                UnconfiguredDevice(
                    device=dev,
                    state=state,
                    status=status_clean,
                    status_badge=status_badge,
                    details=details,
                )
            )

    return ScanReport(
        configured_devices=configured_devices,
        unconfigured_devices=unconfigured_devices,
        unconfigured_only=unconfigured_only,
    )


class ScanPresenter:
    """Strategy for rendering system storage scan results in JSON or human-readable table."""

    @staticmethod
    def render_table(report: ScanReport, console: Console) -> int:
        configured_devices = report.configured_devices
        unconfigured_devices = report.unconfigured_devices

        # --------------------------------------------------------------------------
        # SECTION 1: CONFIGURED & ACTIVE STORAGE DEVICES (Skipped if unconfigured_only)
        # --------------------------------------------------------------------------
        if not report.unconfigured_only:
            console.rule("=", 135)
            console.print(f"{' ' * 45}{console.green(console.bold('CONFIGURED & ACTIVE STORAGE DEVICES'))}")
            console.rule("=", 135)

            header_cfg = f"{'DEVICE':<14} {'SIZE':<8} {'FS (LABEL)':<22} {'MOUNTPOINT':<24} {'FSTAB':<7} {'OWNER (PERMS)':<20} {'INODES (OVERHEAD)':<26} {'RESERVED SPACE'}"
            console.print(console.bold(header_cfg))
            console.print("-" * 135)

            has_sudo_na = False
            if not configured_devices:
                console.print("  No configured storage devices detected.")
            else:
                for dev, state in configured_devices:
                    dev_path = dev.path
                    size_str = dev.size or (state.device.size if state.device else "Unknown")
                    fs_label = state.fstype or "unknown"
                    if state.label:
                        fs_label = f'{fs_label} ("{state.label}")'
                    fs_label_str = fs_label[:21]

                    mp_str = (state.current_mounts[0] if state.current_mounts else "-")[:23]
                    fstab_cell = f"{console.green('[✓]')}    " if state.in_fstab else f"{console.red('[✗]')}    "

                    owner_perms = "-"
                    if state.mountpoint_owner:
                        perms = state.mountpoint_perms or "???"
                        owner_perms = f"{state.mountpoint_owner} ({perms})"
                    owner_perms_str = owner_perms[:19]

                    # Ext4 metrics
                    if state.fstype == "ext4":
                        if os.geteuid() == 0 and state.inode_table_overhead_bytes is not None and state.reserved_space_bytes is not None:
                            if state.total_inodes is not None:
                                if state.total_inodes >= 1_000_000:
                                    in_cnt = f"{state.total_inodes / 1_000_000:.1f}M"
                                elif state.total_inodes >= 1_000:
                                    in_cnt = f"{state.total_inodes / 1_000:.1f}K"
                                else:
                                    in_cnt = str(state.total_inodes)
                            else:
                                in_cnt = "unknown"

                            overhead_mb = state.inode_table_overhead_bytes / (1024 * 1024)
                            ovh_str = f"{overhead_mb / 1024:.1f} GB" if overhead_mb >= 1024 else f"{overhead_mb:.0f} MB"

                            if state.detected_inode_profile and state.detected_inode_profile != "default":
                                inode_text = f"{in_cnt} [{state.detected_inode_profile}] ({ovh_str})"
                            else:
                                inode_text = f"{in_cnt} ({ovh_str} ovh)"

                            res_gb = state.reserved_space_bytes / (1024 * 1024 * 1024)
                            res_pct = state.reserved_percent if state.reserved_percent is not None else 0.0
                            reserved_text = f"{res_gb:.1f} GB ({res_pct:.1f}%)"

                            inode_cell = f"{inode_text:<26}"
                            reserved_cell = reserved_text
                        else:
                            inode_cell = f"{console.yellow('N/A (sudo required)')}       "
                            reserved_cell = f"{console.yellow('N/A (sudo required)')}"
                            has_sudo_na = True
                    else:
                        inode_cell = f"{'N/A (non-ext4)':<26}"
                        reserved_cell = "N/A (non-ext4)"

                    console.print(f"{dev_path:<14} {size_str:<8} {fs_label_str:<22} {mp_str:<24} {fstab_cell} {owner_perms_str:<20} {inode_cell} {reserved_cell}")

            console.print("-" * 135)
            console.print(f"Total Configured: {console.bold(str(len(configured_devices)))} drive(s) healthy and persistent.")
            if has_sudo_na:
                console.print(f"  {console.yellow('ℹ Note:')} Run with {console.bold('sudo')} to calculate ext4 inode table overhead and reserved space.")
            console.print("")

        # --------------------------------------------------------------------------
        # SECTION 2: UNCONFIGURED / AVAILABLE STORAGE DEVICES (Always rendered)
        # --------------------------------------------------------------------------
        console.rule("=", 135)
        console.print(f"{' ' * 44}{console.cyan(console.bold('UNCONFIGURED / AVAILABLE STORAGE DEVICES'))}")
        console.rule("=", 135)

        header_unc = f"{'DEVICE':<14} {'SIZE':<8} {'TYPE':<6} {'MODEL':<22} {'FS':<8} {'STATUS':<24} {'DETAILS'}"
        console.print(console.bold(header_unc))
        console.print("-" * 135)

        if not unconfigured_devices:
            console.print(f"  {console.green('[OK]')} No unconfigured or orphaned storage devices detected.")
        else:
            for item in unconfigured_devices:
                dev = item.device
                state = item.state
                model = ""
                if state.device and state.device.model:
                    model = state.device.model
                elif state.parent_device and state.parent_device.model:
                    model = state.parent_device.model
                elif dev.model:
                    model = dev.model

                model_str = (model or "Generic")[:20]
                fs_str = (state.fstype or "-")[:7]

                details = item.details
                if state.label:
                    details = f"Label: '{state.label}', {details}"

                if item.status == "RAW_UNFORMATTED":
                    colored_badge = console.yellow(item.status_badge)
                elif item.status == "UNCONFIGURED_FS":
                    colored_badge = console.cyan(item.status_badge)
                else:
                    colored_badge = console.yellow(item.status_badge)

                badge_pad = 33 if console.enabled else 24
                console.print(f"{dev.path:<14} {dev.size:<8} {dev.type:<6} {model_str:<22} {fs_str:<8} {colored_badge:<{badge_pad}} {details}")

        console.print("-" * 135)
        if unconfigured_devices:
            first_dev = unconfigured_devices[0].device.path
            console.print(f"Found {console.bold(str(len(unconfigured_devices)))} storage device(s) requiring setup.")
            console.print(f"Example setup: {console.bold(f'sudo ./drive_setup.py -d {first_dev} -m /mnt/storage -l \"storage_pool\" --format')}")
        console.rule("=", 135)
        console.print("")
        return 0

    @staticmethod
    def render_json(report: ScanReport) -> int:
        cfg_out = []
        for dev, st in report.configured_devices:
            cfg_out.append({
                "device": dev.path,
                "size": dev.size or (st.device.size if st.device else None),
                "is_rotational": st.is_rotational,
                "fstype": st.fstype,
                "label": st.label,
                "uuid": st.uuid,
                "mountpoint": st.current_mounts[0] if st.current_mounts else None,
                "mountpoints": st.current_mounts,
                "in_fstab": st.in_fstab,
                "fstab_mountpoint": st.fstab_entry.mountpoint if st.fstab_entry else None,
                "owner": st.mountpoint_owner,
                "perms": st.mountpoint_perms,
                "total_inodes": st.total_inodes,
                "free_inodes": st.free_inodes,
                "used_inodes": st.used_inodes,
                "inode_profile": st.detected_inode_profile,
                "inode_table_overhead_bytes": st.inode_table_overhead_bytes,
                "reserved_space_bytes": st.reserved_space_bytes,
                "reserved_percent": st.reserved_percent,
            })

        unc_out = []
        for item in report.unconfigured_devices:
            dev = item.device
            st = item.state
            model = ""
            if st.device and st.device.model:
                model = st.device.model
            elif st.parent_device and st.parent_device.model:
                model = st.parent_device.model
            elif dev.model:
                model = dev.model

            unc_out.append({
                "device": dev.path,
                "size": dev.size,
                "type": dev.type,
                "is_rotational": st.is_rotational,
                "model": model or None,
                "fstype": st.fstype,
                "label": st.label,
                "uuid": st.uuid,
                "status": item.status,
                "details": f"Label: '{st.label}', {item.details}" if st.label else item.details,
            })

        suggested_cmd = None
        if unc_out:
            first_dev = unc_out[0]["device"]
            suggested_cmd = f"sudo ./drive_setup.py -d {first_dev} -m /mnt/storage -l \"storage_pool\" --format"

        payload = {
            "status": "success",
            "mode": "scan",
            "unconfigured_only": report.unconfigured_only,
            "configured_devices": [] if report.unconfigured_only else cfg_out,
            "unconfigured_devices": unc_out,
            "total_configured": len(cfg_out),
            "total_unconfigured": len(unc_out),
            "requires_action": len(unc_out) > 0,
            "suggested_command": suggested_cmd,
        }
        print(json.dumps(payload, indent=2), flush=True)
        return 0


# ==============================================================================
# SETUP STEP EXECUTORS (LIVE SUBPROCESS STREAMING)
# ==============================================================================
def step_format(
    device: str,
    label: str,
    fstype: str = "ext4",
    inode_reserve_type: str = "largefile",
    reserved_percent: int = 1,
    assume_yes: bool = False,
    console: Optional[Console] = None,
    json_mode: bool = False,
) -> None:
    con = console or Console(stream=sys.stdout)
    if fstype == "ext4":
        cmd = ["mkfs.ext4", "-F", "-L", label]
        if inode_reserve_type in ("largefile", "largefile4"):
            cmd.extend(["-T", inode_reserve_type])
        cmd.extend(["-m", str(reserved_percent), device])
    else:
        cmd = ["mkfs", "-t", fstype, "-L", label, device]

    warning_text = con.red(con.bold(f"WARNING: THIS IS DESTRUCTIVE! All existing data on '{device}' will be erased."))
    exp = (
        f"This will format block device '{device}' as an {fstype} filesystem with volume label '{label}'\n"
        f"                 (Inode Profile: {inode_reserve_type}, Reserved Root: {reserved_percent}%).\n"
        f"                 {warning_text}"
    )
    act = f"Format {device} as {fstype} (Label: {label}, Profile: {inode_reserve_type}, Reserve: {reserved_percent}%)"

    if confirm(exp, act, cmd=cmd, assume_yes=assume_yes, console=con, json_mode=json_mode, step_name="FORMAT"):
        # Ensure device is unmounted first
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(f"{device} "):
                        logger.warning("Device is currently mounted. Unmounting before formatting...")
                        run_cmd(["umount", device], check=True, console=con)
                        break
        except Exception:
            pass

        logger.info(f"Formatting {device} as {fstype} with label '{label}' (Profile: {inode_reserve_type}, Reserve: {reserved_percent}%)...")
        # Stream stdout and stderr directly to console live
        res = run_cmd(cmd, capture_output=False, console=con)
        if res.returncode != 0:
            logger.error(f"mkfs failed with exit code {res.returncode}")
            sys.exit(res.returncode)

        if not json_mode:
            con.print(f"{con.green('[SUCCESS]')} Successfully formatted {device} as {fstype} with label '{label}'.")


def step_tune_reserve_block(
    device: str,
    reserved_percent: int = 1,
    assume_yes: bool = False,
    console: Optional[Console] = None,
    json_mode: bool = False,
) -> None:
    con = console or Console(stream=sys.stdout)
    cmd = ["tune2fs", "-m", str(reserved_percent), device]
    exp = f"This will adjust the filesystem reserved root blocks on '{device}' to {reserved_percent}% via tune2fs."
    act = f"Tune reserved block percentage on {device} to {reserved_percent}%"

    if confirm(exp, act, cmd=cmd, assume_yes=assume_yes, console=con, json_mode=json_mode, step_name="TUNE_RESERVED"):
        logger.info(f"Tuning reserved blocks on {device} to {reserved_percent}% via tune2fs...")
        res = run_cmd(cmd, console=con)
        if res.returncode != 0:
            logger.error(f"tune2fs failed with exit code {res.returncode}")
            sys.exit(res.returncode)

        if not json_mode:
            con.print(f"{con.green('[SUCCESS]')} Reserved blocks percentage successfully updated to {reserved_percent}%.")


def step_mount(
    device: str,
    mountpoint: str,
    assume_yes: bool = False,
    console: Optional[Console] = None,
    json_mode: bool = False,
) -> None:
    con = console or Console(stream=sys.stdout)
    cmd = ["mount", device, mountpoint]
    exp = f"This will verify/create directory '{mountpoint}' and mount device '{device}' to it."
    act = f"Mount {device} to {mountpoint}"

    if confirm(exp, act, cmd=cmd, assume_yes=assume_yes, console=con, json_mode=json_mode, step_name="MOUNT"):
        mp_path = Path(mountpoint)
        mp_path.mkdir(parents=True, exist_ok=True)

        # Check if already mounted
        findmnt = run_cmd(["findmnt", "-no", "SOURCE", "-T", mountpoint], console=con)
        if findmnt.returncode == 0 and findmnt.stdout.strip():
            src = findmnt.stdout.strip()
            if src == device or os.path.realpath(src) == os.path.realpath(device):
                logger.info(f"Device {device} is already mounted to {mountpoint}.")
                return
            else:
                logger.warning(f"Mount point {mountpoint} is occupied by {src}. Mounting over it...")

        logger.info(f"Mounting {device} to {mountpoint}...")
        res = run_cmd(cmd, capture_output=False, console=con)
        if res.returncode != 0:
            logger.error(f"mount failed with exit code {res.returncode}")
            sys.exit(res.returncode)

        if not json_mode:
            con.print(f"{con.green('[SUCCESS]')} Device successfully mounted to {mountpoint}.")


def step_fstab(
    device: str,
    mountpoint: str,
    fstype: str = "ext4",
    assume_yes: bool = False,
    console: Optional[Console] = None,
    json_mode: bool = False,
) -> None:
    con = console or Console(stream=sys.stdout)
    blkid = run_cmd(["blkid", "-s", "UUID", "-o", "value", device], console=con)
    uuid = blkid.stdout.strip() if blkid.returncode == 0 else ""
    uuid_str = uuid if uuid else "<DEVICE_UUID>"
    fstab_line = f"UUID={uuid_str}  {mountpoint}  {fstype}  defaults,nofail  0  2"

    exp = (
        f"This will retrieve the unique UUID of device '{device}' and add a persistent entry\n"
        f"                 to '/etc/fstab' to mount it at '{mountpoint}' automatically on system reboot."
    )
    act = f"Configure persistent mount in /etc/fstab"

    if confirm(exp, act, cmd=f"echo '{fstab_line}' >> /etc/fstab", assume_yes=assume_yes, console=con, json_mode=json_mode, step_name="FSTAB"):
        logger.info(f"Extracting device UUID via blkid for {device}...")
        if not uuid:
            blkid = run_cmd(["blkid", "-s", "UUID", "-o", "value", device], check=True, console=con)
            uuid = blkid.stdout.strip()
            if not uuid:
                logger.error(f"Could not extract UUID for {device}. Is it formatted?")
                sys.exit(1)

        logger.info(f"Device UUID identified: {uuid}")

        existing = FstabManager.find_entry(uuid=uuid, device_path=device, mountpoint=mountpoint)
        if existing:
            logger.warning(f"/etc/fstab entry already exists for this UUID or mountpoint: {existing.raw_line.strip()}. Skipping insertion.")
            return

        logger.info("Appending entry to /etc/fstab with atomic backup...")
        FstabManager.append_entry(uuid=uuid, mountpoint=mountpoint, fstype=fstype, options="defaults,nofail", freq=0, passno=2)
        if not json_mode:
            con.print(f"{con.green('[SUCCESS]')} /etc/fstab successfully configured with UUID={uuid}.")


def step_permissions(
    mountpoint: str,
    username: str,
    assume_yes: bool = False,
    console: Optional[Console] = None,
    json_mode: bool = False,
) -> None:
    con = console or Console(stream=sys.stdout)
    try:
        pw = pwd.getpwnam(username)
        group_name = grp.getgrgid(pw.pw_gid).gr_name
        perm_cmd = f"chown -R {username}:{group_name} {mountpoint} && chmod 755 {mountpoint}"
    except Exception:
        perm_cmd = f"chown -R {username} {mountpoint} && chmod 755 {mountpoint}"

    exp = f"This will set directory ownership of '{mountpoint}' to user '{username}' with standard 755 permissions."
    act = f"Set ownership and permissions on {mountpoint} for {username}"

    if confirm(exp, act, cmd=perm_cmd, assume_yes=assume_yes, console=con, json_mode=json_mode, step_name="PERMS"):
        findmnt = run_cmd(["findmnt", "-no", "SOURCE", "-T", mountpoint], console=con)
        if findmnt.returncode != 0 or not findmnt.stdout.strip():
            logger.error(f"Mount point '{mountpoint}' is not currently active.")
            logger.error("Please mount the drive first before applying permissions.")
            sys.exit(1)

        logger.info(f"Resolving UID/GID for '{username}'...")
        try:
            pw = pwd.getpwnam(username)
            uid, gid = pw.pw_uid, pw.pw_gid
        except KeyError:
            logger.error(f"User '{username}' does not exist on this system.")
            sys.exit(1)

        logger.info(f"Setting ownership on {mountpoint} to {username} ({uid}:{gid}) and permissions 755...")
        os.chown(mountpoint, uid, gid)
        os.chmod(mountpoint, 0o755)
        if not json_mode:
            con.print(f"{con.green('[SUCCESS]')} Mount point ownership and permissions updated successfully (755).")


# ==============================================================================
# SETUP ACTION PLAN PRESENTER
# ==============================================================================
class SetupPresenter:
    """Strategy for rendering storage provisioning action plans in JSON or human-readable format."""

    @staticmethod
    def render_action_plan(plan: ActionPlan, console: Console) -> None:
        console.print(f"\n{console.bold('=' * 70)}")
        console.print(f"                {console.green(console.bold('STORAGE PROVISIONING ACTION PLAN'))}")
        console.print(f"{console.bold('=' * 70)}")
        console.print(f"Target Device:     {console.bold(plan.device)} ({plan.model}, {plan.size})")
        console.print(f"Target Mountpoint: {console.bold(plan.target_mountpoint or '[Not Needed / None]')}")
        console.print(f"Target Owner:      {console.bold(plan.target_user)}")
        console.print(f"Filesystem Type:   {console.bold(plan.fstype)}")
        console.print(f"Filesystem Label:  {console.bold(plan.label or '[Not Needed / None]')}")
        if plan.fstype == "ext4":
            console.print(f"Inode Profile:     {console.bold(plan.inode_reserve_type)}")
            console.print(f"Reserved Root:     {console.bold(str(plan.reserved_percent) + '%')}")
        console.print(f"Force Mode:        {console.bold(str(plan.force))}")
        console.print("-" * 70)
        console.print(f"{console.bold('PRE-FLIGHT ACTION EVALUATION:')}")

        for d in plan.decisions:
            if d.action == "EXECUTE":
                badge = console.green("[EXECUTE]")
            elif d.action == "FORCE_OVERRIDE":
                badge = console.red("[OVERRIDE]")
            else:
                badge = console.yellow("[SKIP]")
            console.print(f"  [{d.step_id}] {d.name:<13} {badge} {d.reason}")
            if d.sub_text:
                console.print(f"                    {d.sub_text}")

        console.print("-" * 70)
        console.print(f"Summary: {console.bold(str(plan.execute_count))} action(s) to execute | {console.bold(str(plan.skip_count))} action(s) skipped (already configured).")
        if plan.skip_count > 0 and not plan.force:
            console.print("Pass '--force' if you wish to override skipped actions.")
        console.print(f"{console.bold('=' * 70)}\n")

    @staticmethod
    def render_json(plan: ActionPlan) -> None:
        payload = {
            "status": "success",
            "mode": "setup_plan",
            "device": plan.device,
            "model": plan.model,
            "size": plan.size,
            "mountpoint": plan.target_mountpoint,
            "owner": plan.target_user,
            "fstype": plan.fstype,
            "label": plan.label,
            "inode_profile": plan.inode_reserve_type if plan.fstype == "ext4" else None,
            "reserved_percent": plan.reserved_percent if plan.fstype == "ext4" else None,
            "force": plan.force,
            "execute_count": plan.execute_count,
            "skip_count": plan.skip_count,
            "decisions": [
                {
                    "step_id": d.step_id,
                    "name": d.name,
                    "action": d.action,
                    "reason": d.reason,
                    "sub_text": d.sub_text,
                }
                for d in plan.decisions
            ],
        }
        print(json.dumps(payload, indent=2), flush=True)


# ==============================================================================
# MAIN SETUP WORKFLOW WITH ACTION PLAN MATRIX
# ==============================================================================
def run_setup(args: argparse.Namespace, console: Console) -> None:
    json_mode = args.json
    if os.geteuid() != 0:
        ErrorPresenter.render_error(
            error_code="E_PERMISSION_DENIED",
            message="Storage setup stages require administrative privileges (root).",
            remediation=f"sudo {' '.join(sys.argv)}",
            console=console,
            json_mode=json_mode,
        )
        sys.exit(1)

    input_device = args.device
    mountpoint = args.mountpoint
    label = args.label
    fstype = getattr(args, "type", "ext4") or "ext4"
    inode_reserve_type = getattr(args, "inode_reserve_type", "largefile") or "largefile"
    reserved_percent = getattr(args, "reserved_percent", 1)
    if reserved_percent is None:
        reserved_percent = 1
    username = args.user
    force = args.force
    assume_yes = args.yes

    # 1. Determine cascading stages
    do_format = False
    do_tune = False
    do_mount = False
    do_fstab = False
    do_perms = False

    only_tuning = getattr(args, "tune_reserve_block", False) and not any([args.format, args.mount, args.fstab, args.perms, args.all])

    if args.all or args.format:
        do_format = True
        do_tune = True
        do_mount = True
        do_fstab = True
        do_perms = True
    elif args.tune_reserve_block:
        do_tune = True
        do_mount = not only_tuning
        do_fstab = not only_tuning
        do_perms = not only_tuning
    elif args.mount:
        do_tune = True
        do_mount = True
        do_fstab = True
        do_perms = True
    elif args.fstab:
        do_fstab = True
        do_perms = True
    elif args.perms:
        do_perms = True

    # 2. Inspect current device state (with smart partition resolution)
    state = DeviceInspector.inspect(input_device, mountpoint, username)

    if not state.is_block_device:
        ErrorPresenter.render_error(
            error_code="E_INVALID_DEVICE",
            message=f"Target device '{input_device}' is not a valid block device on this system.",
            remediation="Check available block devices using './drive_setup.py --scan --json'.",
            console=console,
            json_mode=json_mode,
        )
        sys.exit(1)

    device = state.device_path

    # Safeguard against accidental whole-disk format when partitions exist
    if do_format and state.device and state.device.type == "disk" and len(state.child_partitions) > 0:
        if not force:
            parts = [p.path for p in state.child_partitions]
            ErrorPresenter.render_error(
                error_code="E_EXISTING_PARTITIONS",
                message=f"Target '{input_device}' contains existing partition(s): {', '.join(parts)}. Formatting the whole disk will destroy the partition table.",
                remediation=f"To format a partition, pass '-d {state.child_partitions[0].path}'. To wipe the entire disk, pass '--force'.",
                console=console,
                json_mode=json_mode,
                extra={"partitions": parts},
            )
            sys.exit(1)

    # Safeguard: if device already contains a filesystem that doesn't match desired state, require --force
    if do_format and state.is_formatted:
        detected_profile = state.detected_inode_profile or "default"
        is_already_target = (
            state.fstype == fstype
            and state.label == label
            and (fstype != "ext4" or detected_profile == inode_reserve_type)
        )
        if not is_already_target and not force:
            dev_profile_str = f" | Inode Profile: {detected_profile}" if state.fstype == "ext4" else ""
            target_profile_str = f" | Inode Profile: {inode_reserve_type}" if fstype == "ext4" else ""
            cmd_suggestion = f"sudo ./drive_setup.py -d {input_device} -m {mountpoint} -l \"{label}\" -t {fstype} -irt {inode_reserve_type} -r {reserved_percent} -fmt -f"
            ErrorPresenter.render_error(
                error_code="E_ACTIVE_FILESYSTEM",
                message=f"Target device '{input_device}' already contains an active filesystem ({state.fstype}, Label: {state.label or 'none'}{dev_profile_str}). Reformatting will permanently destroy all existing contents.",
                remediation=f"If you intend to overwrite this drive, explicitly pass '--force' ('-f'):\n        {cmd_suggestion}",
                console=console,
                json_mode=json_mode,
            )
            sys.exit(1)

    # 3. Strict Backward Prerequisite Validation
    if do_mount and not do_format and not state.is_formatted:
        ErrorPresenter.render_error(
            error_code="E_UNFORMATTED_DRIVE",
            message=f"Prerequisite check failed for '--mount': Device '{device}' has no recognizable filesystem (unformatted).",
            remediation=f"sudo ./drive_setup.py -d {device} -m {mountpoint} -l <label> -t {fstype} --format",
            console=console,
            json_mode=json_mode,
        )
        sys.exit(1)

    if do_fstab and not do_format and not state.is_formatted:
        ErrorPresenter.render_error(
            error_code="E_UNFORMATTED_DRIVE",
            message=f"Prerequisite check failed for '--fstab': Device '{device}' is unformatted and has no UUID.",
            remediation=f"sudo ./drive_setup.py -d {device} -m {mountpoint} -l <label> -t {fstype} --format",
            console=console,
            json_mode=json_mode,
        )
        sys.exit(1)

    if do_perms and not do_mount and not state.mountpoint_is_mounted:
        ErrorPresenter.render_error(
            error_code="E_MOUNTPOINT_NOT_MOUNTED",
            message=f"Prerequisite check failed for '--perms': Target mount point '{mountpoint}' is not actively mounted.",
            remediation="Mount the drive first before applying permissions.",
            console=console,
            json_mode=json_mode,
        )
        sys.exit(1)

    if do_tune and not do_format:
        if not state.is_formatted or state.fstype != "ext4":
            ErrorPresenter.render_error(
                error_code="E_NOT_EXT4",
                message=f"Prerequisite check failed for '--tune-reserve-block': Device '{device}' is not ext4 (current: {state.fstype or 'unformatted'}).",
                remediation="tune2fs reserved block tuning is only supported on ext4 filesystems.",
                console=console,
                json_mode=json_mode,
            )
            sys.exit(1)

    # Validate label requirement when formatting is part of the plan
    if do_format and not label:
        if not state.is_formatted or force:
            ErrorPresenter.render_error(
                error_code="E_MISSING_LABEL",
                message="Filesystem label is required when formatting.",
                remediation="Please specify a label using '-l <label>' (e.g. -l \"ArchiveStorage\").",
                console=console,
                json_mode=json_mode,
            )
            sys.exit(1)

    # 4. Build Pre-Flight Action Plan Matrix
    decisions: List[StepDecision] = []

    format_will_execute = False
    # Step 1: Format
    if do_format:
        detected_profile = state.detected_inode_profile or "default"
        is_already_target = (
            state.is_formatted
            and state.fstype == fstype
            and state.label == label
            and (fstype != "ext4" or detected_profile == inode_reserve_type)
        )
        if is_already_target:
            if force:
                format_will_execute = True
                dec = StepDecision(len(decisions) + 1, "FORMAT", "FORCE_OVERRIDE", f"Reformatting existing {fstype} filesystem '{label}' ({detected_profile} inodes) (--force enabled)")
            else:
                dec = StepDecision(len(decisions) + 1, "FORMAT", "SKIP", f"Device is already {fstype} '{label}' with matching {detected_profile} inodes (pass -f to reformat)")
        elif state.is_formatted:
            format_will_execute = True
            mismatch_items = []
            if state.fstype != fstype:
                mismatch_items.append(f"fs '{state.fstype}' -> '{fstype}'")
            if state.label != label:
                mismatch_items.append(f"label '{state.label}' -> '{label}'")
            if state.fstype == "ext4" and fstype == "ext4" and detected_profile != inode_reserve_type:
                mismatch_items.append(f"inodes '{detected_profile}' -> '{inode_reserve_type}'")
            mismatch_str = ", ".join(mismatch_items) if mismatch_items else f"wiping existing '{state.fstype}'"
            dec = StepDecision(len(decisions) + 1, "FORMAT", "FORCE_OVERRIDE", f"Reformatting existing filesystem ({mismatch_str}) (--force enabled)")
        else:
            format_will_execute = True
            dec = StepDecision(len(decisions) + 1, "FORMAT", "EXECUTE", f"Format raw device as {fstype} with label '{label}' ({inode_reserve_type} inodes, -m {reserved_percent}%)")

        if format_will_execute and fstype == "ext4":
            dec.sub_text = f"↳ TUNE_RESERVED: {console.cyan('[INCLUDED]')} {reserved_percent}% root reserved space set natively via mkfs.ext4 (-m {reserved_percent})"

        decisions.append(dec)

    # Step: Tune Reserved Blocks
    if do_tune and not format_will_execute:
        if fstype != "ext4":
            decisions.append(StepDecision(len(decisions) + 1, "TUNE_RESERVED", "SKIP", f"Not applicable for {fstype} filesystem"))
        else:
            already_target_pct = (state.reserved_percent is not None and abs(state.reserved_percent - reserved_percent) < 0.05)
            if already_target_pct:
                if force:
                    decisions.append(StepDecision(len(decisions) + 1, "TUNE_RESERVED", "FORCE_OVERRIDE", f"Reserved blocks already at {reserved_percent}%; reapplying (--force enabled)"))
                else:
                    decisions.append(StepDecision(len(decisions) + 1, "TUNE_RESERVED", "SKIP", f"Reserved blocks already set to {reserved_percent}%"))
            else:
                curr_pct_str = f"{state.reserved_percent:.1f}%" if state.reserved_percent is not None else "unknown"
                decisions.append(StepDecision(len(decisions) + 1, "TUNE_RESERVED", "EXECUTE", f"Tune root reserved blocks from {curr_pct_str} to {reserved_percent}% via tune2fs"))

    # Step: Mount
    if do_mount:
        if mountpoint in state.current_mounts:
            if force:
                decisions.append(StepDecision(len(decisions) + 1, "MOUNT", "FORCE_OVERRIDE", f"Device is already mounted; will remount to {mountpoint} (--force enabled)"))
            else:
                decisions.append(StepDecision(len(decisions) + 1, "MOUNT", "SKIP", f"Device is already mounted to target {mountpoint}"))
        else:
            decisions.append(StepDecision(len(decisions) + 1, "MOUNT", "EXECUTE", f"Mount {device} to {mountpoint}"))

    # Step: Fstab
    if do_fstab:
        if state.in_fstab and state.fstab_entry and state.fstab_entry.mountpoint == mountpoint:
            if force:
                decisions.append(StepDecision(len(decisions) + 1, "FSTAB", "FORCE_OVERRIDE", f"Persistent entry already exists; will rewrite (--force enabled)"))
            else:
                decisions.append(StepDecision(len(decisions) + 1, "FSTAB", "SKIP", f"Matching persistent entry already active in /etc/fstab for {mountpoint}"))
        else:
            decisions.append(StepDecision(len(decisions) + 1, "FSTAB", "EXECUTE", f"Add persistent mount entry for UUID to /etc/fstab"))

    # Step: Perms
    if do_perms:
        perms_already_correct = state.mountpoint_owner == username and state.mountpoint_perms in ("755", "775")
        if perms_already_correct:
            if force:
                decisions.append(StepDecision(len(decisions) + 1, "PERMS", "FORCE_OVERRIDE", f"Ownership is already {username}:{state.mountpoint_perms}; reapplying (--force enabled)"))
            else:
                decisions.append(StepDecision(len(decisions) + 1, "PERMS", "SKIP", f"Ownership is already {username} with permissions {state.mountpoint_perms}"))
        else:
            decisions.append(StepDecision(len(decisions) + 1, "PERMS", "EXECUTE", f"Set ownership to {username} and permissions to 755"))

    dev_model = "Generic"
    if state.device and state.device.model:
        dev_model = state.device.model
    elif state.parent_device and state.parent_device.model:
        dev_model = state.parent_device.model
    dev_size = state.device.size if state.device and state.device.size else "Unknown"

    execute_count = sum(1 for d in decisions if d.action in ("EXECUTE", "FORCE_OVERRIDE"))
    skip_count = sum(1 for d in decisions if d.action == "SKIP")

    plan = ActionPlan(
        device=device,
        target_mountpoint=mountpoint,
        target_user=username,
        fstype=fstype,
        label=label,
        inode_reserve_type=inode_reserve_type,
        reserved_percent=reserved_percent,
        force=force,
        decisions=decisions,
        model=dev_model,
        size=dev_size,
        execute_count=execute_count,
        skip_count=skip_count,
    )

    if not json_mode:
        SetupPresenter.render_action_plan(plan, console)

    if execute_count == 0:
        if json_mode:
            print(json.dumps({
                "status": "success",
                "mode": "setup",
                "message": "All requested operations are already complete. Nothing to do!",
                "device": device,
                "mountpoint": mountpoint,
                "actions_executed": 0,
            }, indent=2), flush=True)
        else:
            console.print(f"{console.green(console.bold('✓ All requested operations are already complete. Nothing to do!'))}\n")
        return

    # 6. Execute Scheduled Steps
    for d in decisions:
        if d.action in ("EXECUTE", "FORCE_OVERRIDE"):
            if d.name == "FORMAT":
                step_format(device, label, fstype, inode_reserve_type, reserved_percent, assume_yes, console=console, json_mode=json_mode)
            elif d.name == "TUNE_RESERVED":
                step_tune_reserve_block(device, reserved_percent, assume_yes, console=console, json_mode=json_mode)
            elif d.name == "MOUNT":
                step_mount(device, mountpoint, assume_yes, console=console, json_mode=json_mode)
            elif d.name == "FSTAB":
                step_fstab(device, mountpoint, fstype, assume_yes, console=console, json_mode=json_mode)
            elif d.name == "PERMS":
                step_permissions(mountpoint, username, assume_yes, console=console, json_mode=json_mode)

    if json_mode:
        print(json.dumps({
            "status": "success",
            "mode": "setup",
            "device": device,
            "mountpoint": mountpoint,
            "actions_executed": execute_count,
        }, indent=2), flush=True)
    else:
        console.print(f"\n{console.green(console.bold('✓ Script execution completed successfully.'))}")
        if mountpoint:
            console.print(f"Drive '{device}' is fully configured and ready at '{mountpoint}'.\n")
        else:
            console.print(f"Drive '{device}' operations completed successfully.\n")


# ==============================================================================
# APM TUNING ROUTINE WITH BREADCRUMB TELEMETRY
# ==============================================================================
def do_tune_apm(
    device: str,
    target_apm_str: str,
    assume_yes: bool,
    console: Console,
    breadcrumbs: BreadcrumbPublisher,
    json_mode: bool = False,
) -> int:
    """
    Tuning routine for ATA Advanced Power Management (APM) on rotational hard drives.
    Provides context-aware thermal warnings and dispatches an event breadcrumb to Alloy/Loki.
    """
    logger.info(f"Targeting APM tuning for device: {device} -> {target_apm_str}")

    eff_path, target_dev, parent_dev, _, _ = DeviceInspector.resolve_target(device)
    disk_path = parent_dev.path if parent_dev else (target_dev.path if target_dev else device)
    disk_name = os.path.basename(os.path.realpath(disk_path))

    # 1. Validation: Block Device Exists
    dev_obj = Path(disk_path)
    if not dev_obj.exists() or not dev_obj.is_block_device():
        ErrorPresenter.render_error(
            error_code="E_INVALID_DEVICE",
            message=f"Target disk '{disk_path}' is not a valid block device on this system.",
            remediation="Use './drive_setup.py --scan' to discover valid disks.",
            console=console,
            json_mode=json_mode,
        )
        return 1

    # 2. Validation: Rotational HDD
    is_rotational = DeviceInspector.is_device_rotational(disk_path)
    if not is_rotational:
        ErrorPresenter.render_error(
            error_code="E_NOT_ROTATIONAL",
            message=f"Device '{disk_path}' is a non-rotational Solid State Drive (SSD/NVMe).",
            remediation="ATA APM power management is only applicable to rotational hard disk drives.",
            console=console,
            json_mode=json_mode,
        )
        return 1

    # 3. Validation: Root Privileges
    if os.geteuid() != 0:
        ErrorPresenter.render_error(
            error_code="E_PERMISSION_DENIED",
            message="APM tuning requires root privileges to modify ATA controller registers.",
            remediation=f"Re-run command with sudo: sudo {' '.join(sys.argv)}",
            console=console,
            json_mode=json_mode,
        )
        return 1

    # 4. Validation: hdparm availability
    if not shutil.which("hdparm"):
        ErrorPresenter.render_error(
            error_code="E_MISSING_HDPARM",
            message="The 'hdparm' utility is required to inspect and tune APM.",
            remediation="Install hdparm: sudo apt install hdparm",
            console=console,
            json_mode=json_mode,
        )
        return 1

    # 5. Query Current APM Level
    apm_supp, curr_level, curr_label, _ = DeviceInspector.get_apm_info(disk_path, is_rotational=True)

    # 6. Parse and Validate Target APM Level
    val_clean = str(target_apm_str).strip().lower()
    if val_clean not in ("off", "disabled") and not val_clean.isdigit():
        ErrorPresenter.render_error(
            error_code="E_INVALID_APM_VALUE",
            message=f"Invalid APM value '{target_apm_str}'.",
            remediation="Specify an integer from 1 to 255 (e.g. 128, 254) or 'off'.",
            console=console,
            json_mode=json_mode,
        )
        return 1

    # 7. Context-Aware Thermal vs Mechanical Advisory
    if val_clean in ("254", "off", "disabled", "255"):
        thermal_advisory = (
            "Setting APM to 254 keeps drive heads loaded continuously, eliminating wake latency "
            "and load/unload cycle wear. NOTICE: Idle power draw increases by ~2-4W, raising drive "
            "operating temperatures by 5°C to 8°C. Ensure adequate chassis airflow."
        )
    elif val_clean == "128":
        thermal_advisory = (
            "Setting APM to 128 disables spindown while allowing heads to park during idle according "
            "to firmware timers. This balances mechanical wear and thermal dissipation (runs cooler)."
        )
    else:
        thermal_advisory = f"Configuring ATA APM level {target_apm_str} on {disk_path}."

    # 8. User Confirmation
    cmd = ["hdparm", "-B", str(target_apm_str), disk_path]
    confirm(
        explanation=thermal_advisory,
        action_message=f"Tune ATA APM level on {disk_path} to {target_apm_str}",
        cmd=cmd,
        assume_yes=assume_yes,
        console=console,
        json_mode=json_mode,
        step_name="TUNE_APM",
    )

    # 9. Execute hdparm
    logger.info(f"Applying APM level {target_apm_str} to {disk_path} via hdparm...")
    res = run_cmd(cmd, check=False, console=console)
    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip()
        ErrorPresenter.render_error(
            error_code="E_HDPARM_FAILED",
            message=f"Failed to set APM level on {disk_path}: {err_msg}",
            remediation="Verify disk is connected via native SATA or a bridge that supports SAT passthrough.",
            console=console,
            json_mode=json_mode,
        )
        return 1

    # 10. Query Updated APM Status
    _, new_level, new_label, _ = DeviceInspector.get_apm_info(disk_path, is_rotational=True)

    # 11. Dispatch Breadcrumb Event to Alloy/Loki
    telemetry_sent = breadcrumbs.publish(
        action="apm_tune",
        device=disk_path,
        payload={
            "old_apm": curr_level,
            "new_apm": target_apm_str,
            "resolved_level": new_level,
            "note": thermal_advisory,
        },
    )

    udev_snippet = (
        f'ACTION=="add", SUBSYSTEM=="block", KERNEL=="{disk_name}", '
        f'ATTR{{queue/rotational}}=="1", RUN+="/sbin/hdparm -B {target_apm_str} /dev/%k"'
    )

    # 12. Render Results
    if json_mode:
        payload = {
            "status": "success",
            "mode": "tune_apm",
            "device": disk_path,
            "old_apm": curr_level,
            "new_apm": target_apm_str,
            "resolved_status": new_label,
            "telemetry_sent": telemetry_sent,
            "persistence_hint": f"To persist across reboots, add to /etc/udev/rules.d/69-hdparm.rules: {udev_snippet}",
        }
        print(json.dumps(payload, indent=2), flush=True)
        return 0
    else:
        console.print(f"\n{console.green(console.bold('✓ APM level successfully updated:'))} {disk_path} -> {console.bold(new_label)}")
        if telemetry_sent:
            console.print(f"  [{console.green('✓')}] Telemetry breadcrumb dispatched to Alloy/Loki ({breadcrumbs.endpoint_url})")
        elif breadcrumbs.enabled:
            console.print(f"  [{console.dim('ℹ')}] Telemetry hook attempted (endpoint unreachable or timed out)")

        console.print(f"\n{console.bold('Persistence Notice:')}")
        console.print("  hdparm runtime settings are volatile and reset upon system reboot.")
        console.print("  To persist this setting across reboots, add a rule in /etc/udev/rules.d/69-hdparm.rules:")
        console.print(f"    {console.bold(udev_snippet)}\n")
        return 0


# ==============================================================================
# CLI PARSER DEFINITION
# ==============================================================================
def create_parser() -> argparse.ArgumentParser:
    current_host = socket.gethostname()
    default_user = get_default_user()
    parser = argparse.ArgumentParser(
        prog="drive_setup.py",
        description=f"Homelab Storage Setup & Provisioning Tool for {current_host}",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    setup_group = parser.add_argument_group("Setup Options")
    setup_group.add_argument("-d", "--device", help="Target block device (e.g., /dev/sdb, /dev/sdb1, /dev/nvme1n1p1)")
    setup_group.add_argument("-m", "--mountpoint", help="Target mount point directory (e.g., /mnt/storage)")
    setup_group.add_argument("-l", "--label", help="Filesystem label for formatting (required for --format)")
    setup_group.add_argument("-t", "--type", default="ext4", help="Target filesystem type for formatting (default: ext4)")
    setup_group.add_argument("-irt", "--inode-reserve-type", choices=["largefile", "largefile4", "default"], default="largefile", help="Ext4 inode ratio profile for media storage (largefile: 1MB/inode, largefile4: 4MB/inode, default: 16KB/inode)")
    setup_group.add_argument("-r", "--reserved-percent", type=int, default=1, help="Filesystem reserved root blocks percentage (default: 1, homelab media standard)")
    setup_group.add_argument("-u", "--user", default=default_user, help=f"User who will own the mount point (default: {default_user})")
    setup_group.add_argument("--tune-apm", help="Tune ATA Advanced Power Management level on rotational HDDs (e.g. 128, 254, off)")
    setup_group.add_argument("--alloy-url", default=os.getenv("ALLOY_URL", "http://127.0.0.1:9999"), help="HTTP endpoint for Grafana Alloy / Loki push (default: http://127.0.0.1:9999 or ALLOY_URL env var)")
    setup_group.add_argument("--no-telemetry", action="store_true", help="Disable sending event breadcrumbs to Alloy/Loki")
    setup_group.add_argument("-y", "--yes", action="store_true", help="Skip interactive confirmation prompts")
    setup_group.add_argument("-f", "--force", action="store_true", help="Force operations (override data protection and remount)")
    setup_group.add_argument("-v", "--verbose", action="store_true", default=True, help="Enable verbose debug logging (default: True)")
    setup_group.add_argument("-q", "--quiet", action="store_true", help="Quiet mode: suppress debug command logs and execution output")
    setup_group.add_argument("--color", choices=["auto", "always", "never"], default="auto", help="Color output mode (auto: detected via TTY/NO_COLOR, always, never)")

    mode_group = parser.add_argument_group("Audit & Discovery Modes")
    mode_group.add_argument("-V", "--verify", action="store_true", help="Inspect and verify the setup status of a drive without modifying anything")
    mode_group.add_argument("-s", "--scan", action="store_true", help="Scan system storage devices (displays configured and unconfigured)")
    mode_group.add_argument("-uo", "--unconfigured-only", action="store_true", help="When scanning, only display unconfigured / available storage devices (skips configured table)")
    mode_group.add_argument("--json", action="store_true", help="Output results as structured JSON (for AI agents and automation)")

    steps_group = parser.add_argument_group("Setup Stages (Cascading Lifecycle)")
    steps_group.add_argument("-fmt", "--format", action="store_true", help="Stage 1: Format drive (with inode profile & reserved blocks) and cascade through all stages")
    steps_group.add_argument("-trb", "--tune-reserve-block", action="store_true", help="Stage 2: Tune filesystem reserved root block percentage via tune2fs (online/offline) and cascade")
    steps_group.add_argument("-mnt", "--mount", action="store_true", help="Stage 3: Mount drive and cascade through FSTAB and PERMS")
    steps_group.add_argument("-fst", "--fstab", action="store_true", help="Stage 4: Configure /etc/fstab persistence and cascade to PERMS")
    steps_group.add_argument("-p", "--perms", action="store_true", help="Stage 5: Set mount point directory ownership and standard permissions (755)")
    steps_group.add_argument("-a", "--all", action="store_true", help="Complete pipeline: execute all stages from format through permissions")

    parser.epilog = """Examples:
  # 1. Discover all unconfigured or partially configured storage drives:
  sudo ./drive_setup.py -s

  # 2. Audit current setup status and efficiency metrics of a specific drive:
  ./drive_setup.py -V -d /dev/sdb1 -m /mnt/storage

  # 3. Completely provision a media drive from scratch (largefile inodes, 1% reserve):
  sudo ./drive_setup.py -d /dev/sdc1 -m /mnt/storage -l "storage_pool" -irt largefile -r 1 -fmt -f

  # 4. Tune reserved root blocks on an existing active ext4 drive (reclaiming space live):
  sudo ./drive_setup.py -d /dev/sdb1 -trb -r 1

  # 5. Mount an existing formatted drive and cascade fstab, perms & tuning:
  sudo ./drive_setup.py -d /dev/sdb1 -m /mnt/storage -mnt

  # 6. Tune ATA Advanced Power Management (APM) on an HDD with breadcrumb event:
  sudo ./drive_setup.py -d /dev/sdb --tune-apm 128
"""
    return parser


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    json_mode = args.json
    if json_mode:
        console = Console(color_mode="never", stream=sys.stderr)
        setup_logging(console=console, verbose=False, quiet=True)
    else:
        console = Console(color_mode=args.color, stream=sys.stdout)
        if args.quiet:
            setup_logging(console=console, verbose=False, quiet=True)
        else:
            setup_logging(console=console, verbose=True, quiet=False)

    breadcrumbs = BreadcrumbPublisher(
        source="drive_setup",
        endpoint_url=args.alloy_url,
        enabled=not args.no_telemetry,
    )

    # Mode 1: Scan
    if args.scan or args.unconfigured_only:
        try:
            report = do_scan(unconfigured_only=args.unconfigured_only)
        except StorageSetupError as e:
            ErrorPresenter.render_error(
                error_code=e.error_code,
                message=e.message,
                remediation=e.remediation,
                console=console,
                json_mode=json_mode,
                extra=e.extra,
            )
            sys.exit(1)

        if json_mode:
            sys.exit(ScanPresenter.render_json(report))
        else:
            sys.exit(ScanPresenter.render_table(report, console))

    # Mode 2: Verify
    if args.verify:
        if not args.device:
            ErrorPresenter.render_error(
                error_code="E_MISSING_DEVICE",
                message="Target device is required for --verify mode.",
                remediation="Specify device using '-d <device>' (e.g. -d /dev/sdb).",
                console=console,
                json_mode=json_mode,
            )
            if not json_mode:
                parser.print_help(sys.stderr)
            sys.exit(1)

        try:
            report = do_verify(args.device, args.mountpoint, args.user)
        except StorageSetupError as e:
            ErrorPresenter.render_error(
                error_code=e.error_code,
                message=e.message,
                remediation=e.remediation,
                console=console,
                json_mode=json_mode,
                extra=e.extra,
            )
            sys.exit(1)

        if json_mode:
            sys.exit(VerifyPresenter.render_json(report))
        else:
            sys.exit(VerifyPresenter.render_table(report, console))

    # Mode 3: Tune APM
    if args.tune_apm is not None:
        if not args.device:
            ErrorPresenter.render_error(
                error_code="E_MISSING_DEVICE",
                message="Target device is required for --tune-apm.",
                remediation="Specify device using '-d <device>' (e.g. -d /dev/sdb).",
                console=console,
                json_mode=json_mode,
            )
            if not json_mode:
                parser.print_help(sys.stderr)
            sys.exit(1)

        sys.exit(do_tune_apm(
            device=args.device,
            target_apm_str=args.tune_apm,
            assume_yes=args.yes,
            console=console,
            breadcrumbs=breadcrumbs,
            json_mode=json_mode,
        ))

    # Mode 4: Setup Actions
    has_step = any([args.format, args.mount, args.fstab, args.perms, args.tune_reserve_block, args.all])
    if not has_step:
        ErrorPresenter.render_error(
            error_code="E_MISSING_STAGE",
            message="No setup stage specified.",
            remediation="Specify a starting stage (-fmt, -mnt, -fst, -p, -trb), '--tune-apm <LEVEL>', or use '-a / --all'.",
            console=console,
            json_mode=json_mode,
        )
        if not json_mode:
            parser.print_help(sys.stderr)
        sys.exit(1)

    if not args.device:
        ErrorPresenter.render_error(
            error_code="E_MISSING_DEVICE",
            message="Target device is required for setup.",
            remediation="Specify device using '-d <device>' (e.g. -d /dev/sdb).",
            console=console,
            json_mode=json_mode,
        )
        if not json_mode:
            parser.print_help(sys.stderr)
        sys.exit(1)

    only_tuning = args.tune_reserve_block and not any([args.format, args.mount, args.fstab, args.perms, args.all])
    if not args.mountpoint and not only_tuning:
        ErrorPresenter.render_error(
            error_code="E_MISSING_MOUNTPOINT",
            message="Target mount point directory is required for setup.",
            remediation="Specify mount point using '-m <path>' (e.g. -m /mnt/storage).",
            console=console,
            json_mode=json_mode,
        )
        if not json_mode:
            parser.print_help(sys.stderr)
        sys.exit(1)

    run_setup(args, console=console)


if __name__ == "__main__":
    main()
