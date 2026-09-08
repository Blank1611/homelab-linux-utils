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
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

# ==============================================================================
# TERMINAL COLORS & FORMATTING
# ==============================================================================
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BLUE = "\033[34m"
NC = "\033[0m"


# ==============================================================================
# LOGGING CONFIGURATION (STANDARD POSIX / PYTHON LOGGING)
# ==============================================================================
class StandardColoredFormatter(logging.Formatter):
    """
    Standard POSIX/Python log formatter with ANSI-colored, unpadded level tags.
    Format: %(asctime)s [%(levelname)s] [tid:%(thread)d] [%(name)s]: %(message)s
    """

    LEVEL_COLORS = {
        logging.DEBUG: BLUE,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, NC)
        orig_levelname = record.levelname
        record.levelname = f"{color}{orig_levelname}{NC}"
        formatted = super().format(record)
        record.levelname = orig_levelname
        return formatted


class UnbufferedStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes immediately on every emitted log record."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


logger = logging.getLogger("drive_setup")


def setup_logging(verbose: bool = True, quiet: bool = False) -> None:
    """
    Configure standard logging. Defaults to verbose (DEBUG) for personal homelab use.
    In quiet mode, sets WARNING level to suppress routine probe logs.
    """
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logger.setLevel(level)
    handler = UnbufferedStreamHandler(sys.stdout)
    handler.setLevel(level)
    fmt = "%(asctime)s [%(levelname)s] [tid:%(thread)d] [%(name)s]: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handler.setFormatter(StandardColoredFormatter(fmt=fmt, datefmt=datefmt))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False


# Initialize default logging on module load (defaults to verbose)
setup_logging(verbose=True)

# Compatibility aliases
log_info = logger.info
log_debug = logger.debug
log_warn = logger.warning
log_error = logger.error
log_step = logger.info


# ==============================================================================
# USER RESOLUTION
# ==============================================================================
def get_default_user() -> str:
    """Get invoking user if run under sudo, else system user via getpass."""
    return os.environ.get("SUDO_USER") or getpass.getuser()


# ==============================================================================
# CENTRALIZED SUBPROCESS RUNNER (WITH COMMAND & OUTPUT LOGGING)
# ==============================================================================
def run_cmd(cmd: List[str], check: bool = False, capture_output: bool = True) -> subprocess.CompletedProcess:
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
            print(f"\n{RED}{BOLD}[PERMISSION ERROR]{NC} Command failed. If administrative privileges are needed, re-run with {BOLD}sudo{NC}:", file=sys.stderr)
            print(f"  {BOLD}sudo {' '.join(sys.argv)}{NC}\n", file=sys.stderr)
        if check and res.returncode != 0:
            raise subprocess.CalledProcessError(res.returncode, cmd)
        return res


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


@dataclass
class StepDecision:
    step_id: int
    name: str
    action: str  # "EXECUTE", "SKIP", "FORCE_OVERRIDE"
    reason: str
    sub_text: Optional[str] = None


# ==============================================================================
# USER CONFIRMATION HELPER
# ==============================================================================
def confirm(
    explanation: str,
    action_message: str,
    cmd: Optional[Union[List[str], str]] = None,
    assume_yes: bool = False,
) -> bool:
    print(f"\n--------------------------------------------------", flush=True)
    print(f"{BOLD}UPCOMING ACTION:{NC} {action_message}", flush=True)
    if cmd is not None:
        if isinstance(cmd, list):
            cmd_str = " ".join(cmd)
            print(f"{CYAN}{BOLD}Command:{NC}         {BOLD}{cmd_str}{NC}", flush=True)
            print(f"{CYAN}Exec List:{NC}       {cmd}", flush=True)
        else:
            print(f"{CYAN}{BOLD}Command:{NC}         {BOLD}{cmd}{NC}", flush=True)
    print(f"{YELLOW}Explanation:{NC}     {explanation}", flush=True)
    print(f"--------------------------------------------------", flush=True)

    if assume_yes:
        print(f"{GREEN}[INFO] --yes flag detected. Proceeding automatically...{NC}", flush=True)
        return True

    try:
        response = input("Do you want to proceed with this step? (y/N): ").strip().lower()
        if response in ("y", "yes"):
            return True
        print(f"{RED}[SKIPPED] User cancelled the action.{NC}", flush=True)
        return False
    except (KeyboardInterrupt, EOFError):
        print(f"\n{RED}[ABORTED] Operation interrupted by user.{NC}", file=sys.stderr, flush=True)
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
        print(f"{GREEN}[SUCCESS] /etc/fstab successfully configured with UUID={uuid}.{NC}", flush=True)


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
        )

        for child in data.get("children", []):
            dev.children.append(DeviceInspector._parse_block_device_node(child))

        return dev

    @classmethod
    def get_all_block_devices(cls) -> List[BlockDevice]:
        cmd = ["lsblk", "-J", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL"]
        try:
            res = run_cmd(cmd, check=True)
            parsed = json.loads(res.stdout)
            raw_list = parsed.get("blockdevices", [])
            return [cls._parse_block_device_node(item) for item in raw_list]
        except Exception as err:
            logger.error(f"Failed to query block devices via lsblk: {err}")
            return []

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

        return state


# ==============================================================================
# AUDIT MODE (--verify)
# ==============================================================================
def do_verify(device_path: str, target_mountpoint: Optional[str], target_user: Optional[str] = None) -> int:
    target_user = target_user or get_default_user()
    state = DeviceInspector.inspect(device_path, target_mountpoint, target_user)

    print(f"\n{BOLD}======================================================================{NC}", flush=True)
    print(f"                {CYAN}{BOLD}STORAGE DEVICE AUDIT: {state.device_path}{NC}", flush=True)
    print(f"{BOLD}======================================================================{NC}", flush=True)

    if os.geteuid() != 0:
        print(f"{YELLOW}[NOTICE] Audit running without root privileges (sudo).{NC}")
        print(f"         Ext4 geometry metrics (tune2fs) and raw device attributes may be restricted.")
        print(f"         For complete audit details, run with: {BOLD}sudo {' '.join(sys.argv)}{NC}\n", flush=True)

    if not state.is_block_device:
        logger.error(f"Target device '{device_path}' is not a valid block device.")
        return 1

    # Multi-partition warning if whole disk was queried without a clear match
    if state.device and state.device.type == "disk" and len(state.child_partitions) > 1 and not state.resolved_from_parent:
        logger.warning(f"Target '{device_path}' is a disk containing multiple partitions:")
        for p in state.child_partitions:
            print(f"  → {p.path} ({p.size}, {p.fstype or 'no fs'}, mounts: {p.mountpoints})", flush=True)
        print(f"\nPlease specify the exact partition to verify (e.g. -d {state.child_partitions[0].path}).", flush=True)
        return 1

    dev_model = "Generic"
    if state.device and state.device.model:
        dev_model = state.device.model
    elif state.parent_device and state.parent_device.model:
        dev_model = state.parent_device.model

    dev_size = state.device.size if state.device and state.device.size else "Unknown"
    dev_type = state.device.type if state.device and state.device.type else "block"

    if state.resolved_from_parent and state.parent_device:
        dev_header = f"{BOLD}{state.device_path}{NC} (Parent: {state.parent_device.path}, {dev_model}, {dev_size}, {dev_type})"
    else:
        dev_header = f"{BOLD}{state.device_path}{NC} ({dev_model}, {dev_size}, {dev_type})"

    print(f"Target Device:     {dev_header}", flush=True)
    print(f"Target Mountpoint: {BOLD}{target_mountpoint or '[None Specified]'}{NC}", flush=True)
    print(f"Target Owner:      {BOLD}{target_user}{NC}", flush=True)
    print("----------------------------------------------------------------------", flush=True)
    print(f"{BOLD}COMPONENT AUDIT:{NC}", flush=True)

    all_healthy = True
    missing_steps: List[str] = []

    # 1. Filesystem (--format / -l)
    if state.is_formatted:
        print(f"  [{GREEN}✓{NC}] Filesystem (--format):       {BOLD}{state.fstype}{NC} | UUID: {state.uuid or 'none'} | Label (-l): \"{state.label or 'none'}\"", flush=True)
    else:
        print(f"  [{RED}✗{NC}] Filesystem (--format):       {RED}No recognizable filesystem detected (unformatted){NC}", flush=True)
        all_healthy = False
        missing_steps.append("--format -l <label> (format drive as ext4)")

    # 2. Mount status (--mount / -m)
    if target_mountpoint:
        if target_mountpoint in state.current_mounts:
            print(f"  [{GREEN}✓{NC}] Active Mount (--mount):      Mounted at {BOLD}{target_mountpoint}{NC} (-m)", flush=True)
        elif state.is_mounted:
            other_mount = state.current_mounts[0]
            print(f"  [{YELLOW}!{NC}] Active Mount (--mount):      {YELLOW}Mounted at '{other_mount}', expected '{target_mountpoint}'{NC}", flush=True)
            all_healthy = False
            missing_steps.append(f"--mount (remount to {target_mountpoint})")
        else:
            print(f"  [{RED}✗{NC}] Active Mount (--mount):      {RED}Device is currently unmounted{NC}", flush=True)
            all_healthy = False
            missing_steps.append(f"--mount (mount device to {target_mountpoint})")
    else:
        if state.is_mounted:
            print(f"  [{CYAN}ℹ{NC}] Active Mount (--mount):      Currently mounted at: {', '.join(state.current_mounts)}", flush=True)
        else:
            print(f"  [{CYAN}ℹ{NC}] Active Mount (--mount):      Not mounted", flush=True)

    # 3. Persistence (--fstab)
    if state.in_fstab and state.fstab_entry:
        if target_mountpoint:
            if state.fstab_entry.mountpoint == target_mountpoint:
                print(f"  [{GREEN}✓{NC}] Persistence (--fstab):       Valid persistent entry found in /etc/fstab for {BOLD}{target_mountpoint}{NC}", flush=True)
            else:
                print(f"  [{YELLOW}!{NC}] Persistence (--fstab):       {YELLOW}/etc/fstab entry points to '{state.fstab_entry.mountpoint}' instead of '{target_mountpoint}'{NC}", flush=True)
                all_healthy = False
                missing_steps.append(f"--fstab (update /etc/fstab entry to {target_mountpoint})")
        else:
            print(f"  [{GREEN}✓{NC}] Persistence (--fstab):       Found in /etc/fstab: {state.fstab_entry.spec} -> {state.fstab_entry.mountpoint}", flush=True)
    else:
        print(f"  [{RED}✗{NC}] Persistence (--fstab):       {RED}No persistent entry found in /etc/fstab for this device/UUID{NC}", flush=True)
        all_healthy = False
        missing_steps.append("--fstab (add persistent mount entry)")

    # 4. Target Directory Permissions (--perms / -u)
    if target_mountpoint:
        if state.mountpoint_exists:
            owner_matches = state.mountpoint_owner == target_user
            if owner_matches:
                print(f"  [{GREEN}✓{NC}] Permissions (--perms):       Directory exists | Owner (-u): {state.mountpoint_owner} | Perms: {state.mountpoint_perms}", flush=True)
            else:
                print(f"  [{YELLOW}!{NC}] Permissions (--perms):       {YELLOW}Directory exists | Owner: {state.mountpoint_owner} (expected -u: {target_user}) | Perms: {state.mountpoint_perms}{NC}", flush=True)
                missing_steps.append(f"--perms (set ownership to {target_user})")
        else:
            print(f"  [{RED}✗{NC}] Permissions (--perms):       {RED}Directory '{target_mountpoint}' does not exist yet{NC}", flush=True)
            missing_steps.append(f"--perms (create directory and set ownership)")

    if state.fstype == "ext4":
        print("----------------------------------------------------------------------", flush=True)
        print(f"{BOLD}STORAGE EFFICIENCY & ALLOCATION:{NC}", flush=True)

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
            print(f"  Inodes (-irt):                   {total_inodes_str} total ({used_inodes_str} used, {free_inodes_str} free){profile_str} | Table Overhead: {overhead_str}", flush=True)

            res_bytes = state.reserved_space_bytes or 0
            res_gb = res_bytes / (1024 * 1024 * 1024)
            res_pct = state.reserved_percent if state.reserved_percent is not None else 0.0

            print(f"  Reserved Space (-trb / -r):      {res_pct:.1f}% ({res_gb:.1f} GB reserved for root) [Tool Target: 1%]", flush=True)

            if res_pct >= 3.0:
                target_1pct_gb = (res_bytes / (res_pct / 100)) * 0.01 / (1024 * 1024 * 1024) if res_pct > 0 else 0
                reclaim_gb = res_gb - target_1pct_gb
                print(f"  {YELLOW}[!]{NC} Optimization Notice: {res_pct:.0f}% reserved space detected (~{res_gb:.1f} GB).", flush=True)
                print(f"      Reclaim ~{reclaim_gb:.1f} GB by tuning to 1%: {BOLD}sudo ./drive_setup.py -d {state.device_path} -trb -r 1{NC}", flush=True)
        else:
            if os.geteuid() != 0:
                print(f"  {YELLOW}[!]{NC} Ext4 allocation statistics could not be retrieved (permission denied).", flush=True)
                print(f"      To inspect inodes & reserved blocks via tune2fs, please run with {BOLD}sudo{NC}:", flush=True)
                print(f"      {BOLD}sudo {' '.join(sys.argv)}{NC}", flush=True)
            else:
                print(f"  {YELLOW}[!]{NC} tune2fs could not read ext4 geometry for {state.device_path}.", flush=True)

    print("----------------------------------------------------------------------", flush=True)
    if all_healthy and not missing_steps:
        print(f"{GREEN}{BOLD}✓ AUDIT RESULT: Storage setup is complete, healthy, and persistent!{NC}", flush=True)
        print("No setup actions required.", flush=True)
        print(f"{BOLD}======================================================================{NC}\n", flush=True)
        return 0
    else:
        print(f"{YELLOW}{BOLD}⚠ AUDIT RESULT: Storage setup is incomplete or needs adjustment.{NC}", flush=True)
        print("Recommended actions:", flush=True)
        for step in missing_steps:
            print(f"  {BOLD}→{NC} {step}", flush=True)
        cmd_hint = f"sudo ./drive_setup.py -d {state.device_path} -m {target_mountpoint or '/mnt/<dir>'} --all"
        print(f"\nRun suggested actions via: {BOLD}{cmd_hint}{NC}", flush=True)
        print(f"{BOLD}======================================================================{NC}\n", flush=True)
        return 1


# ==============================================================================
# DISCOVERY MODE (--scan)
# ==============================================================================
def do_scan(unconfigured_only: bool = False) -> int:
    logger.info("Scanning system block devices via lsblk...")
    all_devs = DeviceInspector.get_all_block_devices()
    if not all_devs:
        logger.error("No block devices found or lsblk is unavailable.")
        return 1

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

    # 1. Inspect all candidates in memory first (prevents log interleaved pollution)
    configured_devices: List[Tuple[BlockDevice, DeviceState]] = []
    unconfigured_devices: List[Tuple[BlockDevice, DeviceState, str, str]] = []

    for dev in candidates:
        state = DeviceInspector.inspect(dev.path)
        is_fully_configured = state.is_formatted and state.is_mounted and state.in_fstab

        if is_fully_configured:
            configured_devices.append((dev, state))
        else:
            status_badge = ""
            details = ""
            if not state.is_formatted:
                status_badge = f"{YELLOW}[RAW / UNFORMATTED]{NC}"
                details = "Ready for full setup with --format"
            elif not state.is_mounted and not state.in_fstab:
                status_badge = f"{CYAN}[UNCONFIGURED FS]{NC}"
                details = "Formatted, unmounted, missing fstab"
            elif state.is_mounted and not state.in_fstab:
                status_badge = f"{YELLOW}[TEMP MOUNTED]{NC}"
                details = f"Mounted at '{state.current_mounts[0]}', missing fstab"
            elif not state.is_mounted and state.in_fstab:
                status_badge = f"{YELLOW}[UNMOUNTED IN FSTAB]{NC}"
                details = "Listed in fstab, but currently unmounted"

            unconfigured_devices.append((dev, state, status_badge, details))

    # --------------------------------------------------------------------------
    # SECTION 1: CONFIGURED & ACTIVE STORAGE DEVICES (Skipped if unconfigured_only)
    # --------------------------------------------------------------------------
    if not unconfigured_only:
        print(f"\n{BOLD}{'=' * 135}{NC}", flush=True)
        print(f"{' ' * 45}{GREEN}{BOLD}CONFIGURED & ACTIVE STORAGE DEVICES{NC}", flush=True)
        print(f"{BOLD}{'=' * 135}{NC}", flush=True)

        header_cfg = f"{'DEVICE':<14} {'SIZE':<8} {'FS (LABEL)':<22} {'MOUNTPOINT':<24} {'FSTAB':<7} {'OWNER (PERMS)':<20} {'INODES (OVERHEAD)':<26} {'RESERVED SPACE'}"
        print(BOLD + header_cfg + NC, flush=True)
        print("-" * 135, flush=True)

        has_sudo_na = False
        if not configured_devices:
            print("  No configured storage devices detected.", flush=True)
        else:
            for dev, state in configured_devices:
                dev_path = dev.path
                size_str = dev.size or (state.device.size if state.device else "Unknown")
                fs_label = state.fstype or "unknown"
                if state.label:
                    fs_label = f'{fs_label} ("{state.label}")'
                fs_label_str = fs_label[:21]

                mp_str = (state.current_mounts[0] if state.current_mounts else "-")[:23]
                fstab_cell = f"{GREEN}[✓]{NC}    " if state.in_fstab else f"{RED}[✗]{NC}    "

                owner_perms = "-"
                if state.mountpoint_owner:
                    perms = state.mountpoint_perms or "???"
                    owner_perms = f"{state.mountpoint_owner} ({perms})"
                owner_perms_str = owner_perms[:19]

                # Metric 1: Inodes & Table Overhead
                # Metric 2: Reserved Space & Percentage
                if state.fstype == "ext4":
                    if os.geteuid() == 0 and state.inode_table_overhead_bytes is not None and state.reserved_space_bytes is not None:
                        # Inodes count
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
                        inode_cell = f"{YELLOW}N/A (sudo required){NC}       "
                        reserved_cell = f"{YELLOW}N/A (sudo required){NC}"
                        has_sudo_na = True
                else:
                    inode_cell = f"{'N/A (non-ext4)':<26}"
                    reserved_cell = "N/A (non-ext4)"

                print(f"{dev_path:<14} {size_str:<8} {fs_label_str:<22} {mp_str:<24} {fstab_cell} {owner_perms_str:<20} {inode_cell} {reserved_cell}", flush=True)

        print("-" * 135, flush=True)
        print(f"Total Configured: {BOLD}{len(configured_devices)}{NC} drive(s) healthy and persistent.", flush=True)
        if has_sudo_na:
            print(f"  {YELLOW}ℹ Note:{NC} Run with {BOLD}sudo{NC} to calculate ext4 inode table overhead and reserved space.", flush=True)
        print("", flush=True)

    # --------------------------------------------------------------------------
    # SECTION 2: UNCONFIGURED / AVAILABLE STORAGE DEVICES (Always rendered)
    # --------------------------------------------------------------------------
    print(f"{BOLD}{'=' * 135}{NC}", flush=True)
    print(f"{' ' * 44}{CYAN}{BOLD}UNCONFIGURED / AVAILABLE STORAGE DEVICES{NC}", flush=True)
    print(f"{BOLD}{'=' * 135}{NC}", flush=True)

    header_unc = f"{'DEVICE':<14} {'SIZE':<8} {'TYPE':<6} {'MODEL':<22} {'FS':<8} {'STATUS':<24} {'DETAILS'}"
    print(BOLD + header_unc + NC, flush=True)
    print("-" * 135, flush=True)

    if not unconfigured_devices:
        print(f"  {GREEN}[OK] No unconfigured or orphaned storage devices detected.{NC}", flush=True)
    else:
        for dev, state, status_badge, details in unconfigured_devices:
            model = ""
            if state.device and state.device.model:
                model = state.device.model
            elif state.parent_device and state.parent_device.model:
                model = state.parent_device.model
            elif dev.model:
                model = dev.model

            model_str = (model or "Generic")[:20]
            fs_str = (state.fstype or "-")[:7]

            if state.label:
                details = f"Label: '{state.label}', {details}"

            print(f"{dev.path:<14} {dev.size:<8} {dev.type:<6} {model_str:<22} {fs_str:<8} {status_badge:<33} {details}", flush=True)

    print("-" * 135, flush=True)
    if unconfigured_devices:
        first_dev = unconfigured_devices[0][0].path
        print(f"Found {BOLD}{len(unconfigured_devices)}{NC} storage device(s) requiring setup.", flush=True)
        print(f"Example setup: {BOLD}sudo ./drive_setup.py -d {first_dev} -m /mnt/storage -l \"storage_pool\" --format{NC}", flush=True)
    print(f"{BOLD}{'=' * 135}{NC}\n", flush=True)
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
) -> None:
    if fstype == "ext4":
        cmd = ["mkfs.ext4", "-F", "-L", label]
        if inode_reserve_type in ("largefile", "largefile4"):
            cmd.extend(["-T", inode_reserve_type])
        cmd.extend(["-m", str(reserved_percent), device])
    else:
        cmd = ["mkfs", "-t", fstype, "-L", label, device]

    exp = (
        f"This will format block device '{device}' as an {fstype} filesystem with volume label '{label}'\n"
        f"                 (Inode Profile: {inode_reserve_type}, Reserved Root: {reserved_percent}%).\n"
        f"                 {RED}{BOLD}WARNING: THIS IS DESTRUCTIVE! All existing data on '{device}' will be erased.{NC}"
    )
    act = f"Format {device} as {fstype} (Label: {label}, Profile: {inode_reserve_type}, Reserve: {reserved_percent}%)"

    if confirm(exp, act, cmd=cmd, assume_yes=assume_yes):
        # Ensure device is unmounted first
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(f"{device} "):
                        logger.warning("Device is currently mounted. Unmounting before formatting...")
                        run_cmd(["umount", device], check=True)
                        break
        except Exception:
            pass

        logger.info(f"Formatting {device} as {fstype} with label '{label}' (Profile: {inode_reserve_type}, Reserve: {reserved_percent}%)...")
        # Stream stdout and stderr directly to console live
        res = run_cmd(cmd, capture_output=False)
        if res.returncode != 0:
            logger.error(f"mkfs failed with exit code {res.returncode}")
            sys.exit(res.returncode)

        print(f"{GREEN}[SUCCESS] Successfully formatted {device} as {fstype} with label '{label}'.{NC}", flush=True)


def step_tune_reserve_block(device: str, reserved_percent: int = 1, assume_yes: bool = False) -> None:
    cmd = ["tune2fs", "-m", str(reserved_percent), device]
    exp = f"This will adjust the filesystem reserved root blocks on '{device}' to {reserved_percent}% via tune2fs."
    act = f"Tune reserved block percentage on {device} to {reserved_percent}%"

    if confirm(exp, act, cmd=cmd, assume_yes=assume_yes):
        logger.info(f"Tuning reserved blocks on {device} to {reserved_percent}% via tune2fs...")
        res = run_cmd(cmd)
        if res.returncode != 0:
            logger.error(f"tune2fs failed with exit code {res.returncode}")
            sys.exit(res.returncode)

        print(f"{GREEN}[SUCCESS] Reserved blocks percentage successfully updated to {reserved_percent}%.{NC}", flush=True)


def step_mount(device: str, mountpoint: str, assume_yes: bool = False) -> None:
    cmd = ["mount", device, mountpoint]
    exp = f"This will verify/create directory '{mountpoint}' and mount device '{device}' to it."
    act = f"Mount {device} to {mountpoint}"

    if confirm(exp, act, cmd=cmd, assume_yes=assume_yes):
        mp_path = Path(mountpoint)
        mp_path.mkdir(parents=True, exist_ok=True)

        # Check if already mounted
        findmnt = run_cmd(["findmnt", "-no", "SOURCE", "-T", mountpoint])
        if findmnt.returncode == 0 and findmnt.stdout.strip():
            src = findmnt.stdout.strip()
            if src == device or os.path.realpath(src) == os.path.realpath(device):
                logger.info(f"Device {device} is already mounted to {mountpoint}.")
                return
            else:
                logger.warning(f"Mount point {mountpoint} is occupied by {src}. Mounting over it...")

        logger.info(f"Mounting {device} to {mountpoint}...")
        res = run_cmd(cmd, capture_output=False)
        if res.returncode != 0:
            logger.error(f"mount failed with exit code {res.returncode}")
            sys.exit(res.returncode)

        print(f"{GREEN}[SUCCESS] Device successfully mounted to {mountpoint}.{NC}", flush=True)


def step_fstab(device: str, mountpoint: str, fstype: str = "ext4", assume_yes: bool = False) -> None:
    blkid = run_cmd(["blkid", "-s", "UUID", "-o", "value", device])
    uuid = blkid.stdout.strip() if blkid.returncode == 0 else ""
    uuid_str = uuid if uuid else "<DEVICE_UUID>"
    fstab_line = f"UUID={uuid_str}  {mountpoint}  {fstype}  defaults,nofail  0  2"

    exp = (
        f"This will retrieve the unique UUID of device '{device}' and add a persistent entry\n"
        f"                 to '/etc/fstab' to mount it at '{mountpoint}' automatically on system reboot."
    )
    act = f"Configure persistent mount in /etc/fstab"

    if confirm(exp, act, cmd=f"echo '{fstab_line}' >> /etc/fstab", assume_yes=assume_yes):
        logger.info(f"Extracting device UUID via blkid for {device}...")
        if not uuid:
            blkid = run_cmd(["blkid", "-s", "UUID", "-o", "value", device], check=True)
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


def step_permissions(mountpoint: str, username: str, assume_yes: bool = False) -> None:
    try:
        pw = pwd.getpwnam(username)
        group_name = grp.getgrgid(pw.pw_gid).gr_name
        perm_cmd = f"chown -R {username}:{group_name} {mountpoint} && chmod 755 {mountpoint}"
    except Exception:
        perm_cmd = f"chown -R {username} {mountpoint} && chmod 755 {mountpoint}"

    exp = f"This will set directory ownership of '{mountpoint}' to user '{username}' with standard 755 permissions."
    act = f"Set ownership and permissions on {mountpoint} for {username}"

    if confirm(exp, act, cmd=perm_cmd, assume_yes=assume_yes):
        findmnt = run_cmd(["findmnt", "-no", "SOURCE", "-T", mountpoint])
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
        print(f"{GREEN}[SUCCESS] Mount point ownership and permissions updated successfully (755).{NC}", flush=True)


# ==============================================================================
# MAIN SETUP WORKFLOW WITH ACTION PLAN MATRIX
# ==============================================================================
def run_setup(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        logger.error("Storage setup actions require administrative privileges.")
        print(f"\n{RED}{BOLD}[PERMISSION ERROR]{NC} Storage setup stages require administrative privileges (root).", file=sys.stderr)
        print(f"Please re-run this command with {BOLD}sudo{NC}:", file=sys.stderr)
        print(f"  {BOLD}sudo {' '.join(sys.argv)}{NC}\n", file=sys.stderr)
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
        logger.error(f"Target device '{input_device}' is not a valid block device on this system.")
        sys.exit(1)

    device = state.device_path

    # Safeguard against accidental whole-disk format when partitions exist
    if do_format and state.device and state.device.type == "disk" and len(state.child_partitions) > 0:
        if not force:
            logger.error(f"Target '{input_device}' contains existing partition(s):")
            for p in state.child_partitions:
                print(f"  → {p.path} ({p.size}, {p.fstype or 'no fs'})", file=sys.stderr)
            logger.error("Formatting the whole disk will destroy the partition table.")
            logger.error(f"To format a partition instead, pass '-d {state.child_partitions[0].path}'.")
            logger.error("To intentionally wipe the entire disk and all its partitions, pass '--force'.")
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
            logger.error(f"Target device '{input_device}' already contains an active filesystem:")
            dev_profile_str = f" | Inode Profile: {detected_profile}" if state.fstype == "ext4" else ""
            target_profile_str = f" | Inode Profile: {inode_reserve_type}" if fstype == "ext4" else ""
            print(f"        Detected: Filesystem: {state.fstype} | UUID: {state.uuid or 'none'} | Label: {state.label or 'none'}{dev_profile_str}", file=sys.stderr)
            print(f"        Desired:  Filesystem: {fstype} | Label: {label}{target_profile_str}", file=sys.stderr)
            if state.fstype == "ext4" and fstype == "ext4" and state.label == label and detected_profile != inode_reserve_type:
                logger.error(f"Inode profile mismatch detected ('{detected_profile}' on disk vs '{inode_reserve_type}' desired).")
            print(f"\n{RED}{BOLD}[PROTECTION] An active filesystem was detected on '{input_device}'. Reformatting will permanently destroy all existing contents.{NC}", file=sys.stderr)
            logger.error("If you intend to overwrite this drive with the new configuration, explicitly pass '--force' ('-f').")
            cmd_suggestion = f"sudo ./drive_setup.py -d {input_device} -m {mountpoint} -l \"{label}\" -t {fstype} -irt {inode_reserve_type} -r {reserved_percent} -fmt -f"
            print(f"\nRe-run with -f: {BOLD}{cmd_suggestion}{NC}\n", file=sys.stderr)
            sys.exit(1)

    # 3. Strict Backward Prerequisite Validation
    if do_mount and not do_format and not state.is_formatted:
        logger.error("Prerequisite check failed for '--mount':")
        print(f"        Device '{device}' has no recognizable filesystem (unformatted).", file=sys.stderr)
        print(f"        Cannot mount an unformatted drive.", file=sys.stderr)
        print(f"        To format and provision this drive from scratch, run:", file=sys.stderr)
        print(f"        sudo ./drive_setup.py -d {device} -m {mountpoint} -l <label> -t {fstype} --format", file=sys.stderr)
        sys.exit(1)

    if do_fstab and not do_format and not state.is_formatted:
        logger.error("Prerequisite check failed for '--fstab':")
        print(f"        Device '{device}' is unformatted and has no UUID.", file=sys.stderr)
        sys.exit(1)

    if do_perms and not do_mount and not state.mountpoint_is_mounted:
        logger.error("Prerequisite check failed for '--perms':")
        print(f"        Target mount point '{mountpoint}' is not actively mounted.", file=sys.stderr)
        print(f"        Setting permissions on an unmounted directory only affects the host root disk.", file=sys.stderr)
        sys.exit(1)

    if do_tune and not do_format:
        if not state.is_formatted or state.fstype != "ext4":
            logger.error("Prerequisite check failed for '--tune-reserve-block':")
            print(f"        Device '{device}' is not formatted as an ext4 filesystem (current: {state.fstype or 'unformatted'}).", file=sys.stderr)
            print("        tune2fs reserved block tuning is only supported on ext4 filesystems.", file=sys.stderr)
            sys.exit(1)

    # Validate label requirement when formatting is part of the plan
    if do_format and not label:
        if not state.is_formatted or force:
            logger.error("Filesystem label is required when formatting.")
            print(f"        Please specify a label using '-l <label>' (e.g. -l \"ArchiveStorage\").", file=sys.stderr)
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
            # Reached here only when force is True (due to safety check above)
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
            dec.sub_text = f"↳ TUNE_RESERVED: {CYAN}[INCLUDED]{NC} {reserved_percent}% root reserved space set natively via mkfs.ext4 (-m {reserved_percent})"

        decisions.append(dec)

    # Step: Tune Reserved Blocks (only evaluated as a separate step when format is NOT executing)
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

    # 5. Display Upfront Action Plan Matrix
    dev_model = "Generic"
    if state.device and state.device.model:
        dev_model = state.device.model
    elif state.parent_device and state.parent_device.model:
        dev_model = state.parent_device.model
    dev_size = state.device.size if state.device and state.device.size else "Unknown"

    print(f"\n{BOLD}======================================================================{NC}", flush=True)
    print(f"                {GREEN}{BOLD}STORAGE PROVISIONING ACTION PLAN{NC}", flush=True)
    print(f"{BOLD}======================================================================{NC}", flush=True)
    print(f"Target Device:     {BOLD}{device}{NC} ({dev_model}, {dev_size})", flush=True)
    print(f"Target Mountpoint: {BOLD}{mountpoint or '[Not Needed / None]'}{NC}", flush=True)
    print(f"Target Owner:      {BOLD}{username}{NC}", flush=True)
    print(f"Filesystem Type:   {BOLD}{fstype}{NC}", flush=True)
    print(f"Filesystem Label:  {BOLD}{label or '[Not Needed / None]'}{NC}", flush=True)
    if fstype == "ext4":
        print(f"Inode Profile:     {BOLD}{inode_reserve_type}{NC}", flush=True)
        print(f"Reserved Root:     {BOLD}{reserved_percent}%{NC}", flush=True)
    print(f"Force Mode:        {BOLD}{force}{NC}", flush=True)
    print("----------------------------------------------------------------------", flush=True)
    print(f"{BOLD}PRE-FLIGHT ACTION EVALUATION:{NC}", flush=True)

    execute_count = 0
    skip_count = 0

    for d in decisions:
        if d.action == "EXECUTE":
            badge = f"{GREEN}[EXECUTE]{NC}"
            execute_count += 1
        elif d.action == "FORCE_OVERRIDE":
            badge = f"{RED}[OVERRIDE]{NC}"
            execute_count += 1
        else:
            badge = f"{YELLOW}[SKIP]{NC}"
            skip_count += 1
        print(f"  [{d.step_id}] {d.name:<13} {badge} {d.reason}", flush=True)
        if d.sub_text:
            print(f"                    {d.sub_text}", flush=True)

    print("----------------------------------------------------------------------", flush=True)
    print(f"Summary: {BOLD}{execute_count}{NC} action(s) to execute | {BOLD}{skip_count}{NC} action(s) skipped (already configured).", flush=True)
    if skip_count > 0 and not force:
        print("Pass '--force' if you wish to override skipped actions.", flush=True)
    print(f"{BOLD}======================================================================{NC}\n", flush=True)

    if execute_count == 0:
        print(f"{GREEN}{BOLD}✓ All requested operations are already complete. Nothing to do!{NC}\n", flush=True)
        return

    # 6. Execute Scheduled Steps
    for d in decisions:
        if d.action in ("EXECUTE", "FORCE_OVERRIDE"):
            if d.name == "FORMAT":
                step_format(device, label, fstype, inode_reserve_type, reserved_percent, assume_yes)
            elif d.name == "TUNE_RESERVED":
                step_tune_reserve_block(device, reserved_percent, assume_yes)
            elif d.name == "MOUNT":
                step_mount(device, mountpoint, assume_yes)
            elif d.name == "FSTAB":
                step_fstab(device, mountpoint, fstype, assume_yes)
            elif d.name == "PERMS":
                step_permissions(mountpoint, username, assume_yes)

    print(f"\n{GREEN}{BOLD}✓ Script execution completed successfully.{NC}", flush=True)
    if mountpoint:
        print(f"Drive '{device}' is fully configured and ready at '{mountpoint}'.\n", flush=True)
    else:
        print(f"Drive '{device}' operations completed successfully.\n", flush=True)


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
    setup_group.add_argument("-y", "--yes", action="store_true", help="Skip interactive confirmation prompts")
    setup_group.add_argument("-f", "--force", action="store_true", help="Force operations (override data protection and remount)")
    setup_group.add_argument("-v", "--verbose", action="store_true", default=True, help="Enable verbose debug logging (default: True)")
    setup_group.add_argument("-q", "--quiet", action="store_true", help="Quiet mode: suppress debug command logs and execution output")

    mode_group = parser.add_argument_group("Audit & Discovery Modes")
    mode_group.add_argument("-V", "--verify", action="store_true", help="Inspect and verify the setup status of a drive without modifying anything")
    mode_group.add_argument("-s", "--scan", action="store_true", help="Scan system storage devices (displays configured and unconfigured)")
    mode_group.add_argument("-uo", "--unconfigured-only", action="store_true", help="When scanning, only display unconfigured / available storage devices (skips configured table)")

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
"""
    return parser


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if args.quiet:
        setup_logging(verbose=False, quiet=True)
    else:
        setup_logging(verbose=True, quiet=False)

    # Mode 1: Scan
    if args.scan or args.unconfigured_only:
        sys.exit(do_scan(unconfigured_only=args.unconfigured_only))

    # Mode 2: Verify
    if args.verify:
        if not args.device:
            logger.error("Target device is required for --verify mode.")
            print("        Specify device using '-d <device>' (e.g. -d /dev/sdb).\n", file=sys.stderr)
            parser.print_help(sys.stderr)
            sys.exit(1)
        sys.exit(do_verify(args.device, args.mountpoint, args.user))

    # Mode 3: Setup Actions
    has_step = any([args.format, args.mount, args.fstab, args.perms, args.tune_reserve_block, args.all])
    if not has_step:
        logger.error("No setup stage specified.")
        print("        Specify a starting stage (-fmt, -mnt, -fst, -p, -trb) or use '-a / --all'.\n", file=sys.stderr)
        parser.print_help(sys.stderr)
        sys.exit(1)

    if not args.device:
        logger.error("Target device is required for setup.")
        print("        Specify device using '-d <device>' (e.g. -d /dev/sdb).\n", file=sys.stderr)
        parser.print_help(sys.stderr)
        sys.exit(1)

    only_tuning = args.tune_reserve_block and not any([args.format, args.mount, args.fstab, args.perms, args.all])
    if not args.mountpoint and not only_tuning:
        logger.error("Target mount point directory is required for setup.")
        print("        Specify mount point using '-m <path>' (e.g. -m /mnt/storage).\n", file=sys.stderr)
        parser.print_help(sys.stderr)
        sys.exit(1)

    run_setup(args)


if __name__ == "__main__":
    main()
