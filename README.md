# Homelab Linux Utilities 🛠️

A collection of battle-tested, robust Linux utility tools designed for homelab administrators, home server setups, and automated Linux system maintenance. Zero external Python dependencies — pure standard library combined with standard Linux system tooling.

---

## Featured Tool: `drive_setup.py`

`drive_setup.py` is an intelligent, generic storage management and provisioning tool built to automate the end-to-end lifecycle of adding storage drives (SATA HDDs, SSDs, NVMe drives) to a Linux server or homelab node.

It replaces brittle multi-step manual processes (`mkfs`, `tune2fs`, `mkdir`, `mount`, editing `/etc/fstab`, `chown`, `chmod`) with an idempotent, cascading lifecycle pipeline equipped with pre-flight safety checks and audit modes.

### 🌟 Key Highlights

- **Idempotent Pre-Flight Action Matrix:** Evaluates current filesystem, mount, fstab, and permission states before executing anything. Skipped actions are highlighted so you never accidentally reformat or duplicate fstab entries.
- **Cascading Lifecycle Execution:** Trigger any lifecycle stage, and the tool automatically cascades through all subsequent stages:
  - `FORMAT` ➔ `TUNE_RESERVED` ➔ `MOUNT` ➔ `FSTAB` ➔ `PERMS` (or run `--all`)
- **Storage Space Optimization for Media Drives:**
  - **Inode Ratio Tuning (`-irt`):** Ext4 defaults to 16KB per inode, wasting tens of gigabytes of disk space on inode tables for large media/archive disks. Supports `-irt largefile` (1MB/inode) and `largefile4` (4MB/inode), reclaiming substantial usable capacity.
  - **Reserved Root Block Tuning (`-r`):** Reduces ext4's default 5% root block reservation down to 1% (or custom percentage), recovering 50–100GB+ of usable space on multi-terabyte drives. Can also be applied live to active filesystems.
- **Smart Partition Resolution:** Automatically disambiguates parent block devices (e.g., `/dev/sdb`) to child partitions (e.g., `/dev/sdb1`) when only one partition exists, preventing false "unformatted" warnings while preserving parent hardware metadata (model, serial, size).
- **Accidental Wipe Protection:** Multi-partition disks and drives with existing filesystems cannot be formatted without passing `--force`.
- **Bulletproof Persistence:** Adds `/etc/fstab` entries by **UUID** (never volatile `/dev/sdX` device names) and creates timestamped backups (`/etc/fstab.bak.<timestamp>`) prior to any modification.
- **Dynamic Personalization & Caller Resolution:** Automatically personalizes CLI descriptions to the local machine's hostname (`socket.gethostname()`) and defaults directory ownership to the invoking caller (`$SUDO_USER` or current user), with full support for explicit user overrides (`-u / --user`).
- **Live Output Streaming & Detailed Logging:** Real-time stdout/stderr output streaming for long operations (e.g., `mkfs.ext4` inode allocation and journal creation), complete with ANSI-colored logging and a `-q / --quiet` option.
- **Audit & Discovery Modes:** Non-destructive system scan (`--scan`) and comprehensive storage audit (`--verify`).

---

## 📋 Requirements

- Linux (tested on Debian / Ubuntu / Proxmox / modern Linux distributions)
- Python 3.7+ (uses only Python Standard Library)
- Standard system utilities: `util-linux` (`lsblk`, `blkid`, `findmnt`, `mount`, `umount`), `e2fsprogs` (`mkfs.ext4`, `tune2fs`)
- Root privileges (`sudo`) for provisioning operations (auditing/verifying can be run unprivileged)

---

## 🚀 Quick Start & Usage Examples

Make the script executable:
```bash
chmod +x drive_setup.py
```

### 1. Discovery Mode (`--scan` / `-s`, `-uo` / `--unconfigured-only`)

Scans all host block devices to provide an instant inventory of your storage topology without modifying any drives.

By default, `--scan` presents two clean, structured sections:
1. **Configured & Active Storage Devices:** A compact audit table showing active drives with their filesystem, volume label, mountpoint, `/etc/fstab` persistence, directory owner/permissions, and ext4 efficiency metrics.
2. **Unconfigured / Available Storage Devices:** Identifies raw, unpartitioned, unmounted, or orphaned storage, complete with tailored setup command recipes.

```bash
# Full storage inventory (both configured and unconfigured)
sudo ./drive_setup.py -s

# Display only unconfigured / available devices (skips configured table)
sudo ./drive_setup.py -uo
# or
sudo ./drive_setup.py -s -uo
```

Example scan output:
```text
=======================================================================================================================================
                                             CONFIGURED & ACTIVE STORAGE DEVICES
=======================================================================================================================================
DEVICE         SIZE     FS (LABEL)             MOUNTPOINT               FSTAB   OWNER (PERMS)        INODES (OVERHEAD)          RESERVED SPACE
---------------------------------------------------------------------------------------------------------------------------------------
/dev/sdb1      1.8T     ext4 ("storage_pool")  /mnt/storage             [✓]     <username> (755)     122.1M (29.1 GB ovh)       18.6 GB (1.0%)
---------------------------------------------------------------------------------------------------------------------------------------
Total Configured: 1 drive(s) healthy and persistent.

=======================================================================================================================================
                                            UNCONFIGURED / AVAILABLE STORAGE DEVICES
=======================================================================================================================================
DEVICE         SIZE     TYPE   MODEL                  FS       STATUS                   DETAILS
---------------------------------------------------------------------------------------------------------------------------------------
  [OK] No unconfigured or orphaned storage devices detected.
---------------------------------------------------------------------------------------------------------------------------------------
=======================================================================================================================================
```

#### What `--scan` Checks:
- **Device Topology:** Probes all attached SATA, SAS, USB, and NVMe drives and partitions via `lsblk`.
- **Partition & Filesystem State:** Identifies whether drives are raw/unpartitioned, formatted, or have missing filesystems.
- **Mount & Usage Status:** Distinguishes between active, mounted filesystems and orphaned or unmounted storage.
- **Active Drive Health:** Extracts mountpoint directory ownership/permissions and ext4 storage efficiency (total inodes, profiles, and root reservation percentage) directly into the summary table.
- **Smart Recommendations:** Automatically generates tailored, ready-to-run setup commands (pre-filling suggested partitions, labels, and mount points) for any unconfigured drive detected.

---

### 2. Device Audit Mode (`--verify` / `-V`)

Performs a deep, non-destructive health and configuration compliance check on a specific device and target mount point.

```bash
./drive_setup.py -V -d /dev/sdb1 -m /mnt/storage
# or
./drive_setup.py --verify -d /dev/sdb1 -m /mnt/storage
```

#### What `--verify` Checks:
- **Smart Partition Resolution:** Disambiguates parent disks (e.g. `/dev/sdb`) to child partitions (e.g. `/dev/sdb1`), preventing false "unformatted" warnings while verifying hardware model and size.
- **Filesystem Integrity (`--format`):** Queries `blkid` for filesystem type, volume UUID, and assigned label.
- **Active Mount Verification (`--mount`):** Verifies active mounts in `/proc/mounts`, detecting if the drive is unmounted or mounted to the wrong directory.
- **Persistence Verification (`--fstab`):** Validates `/etc/fstab` entries to ensure the device has a permanent, UUID-bound mount point that will survive reboot.
- **Directory Permissions (`--perms`):** Confirms the mount directory exists, verifies ownership against the expected user (`-u`), and validates standard `755` permissions.
- **Storage Efficiency & Geometry (ext4):**
  - **Inode Allocation & Profile (`-irt`):** Inspects total vs. free inodes, calculates exact inode table overhead in MB/GB, and identifies active profile (`largefile`, `largefile4`, `default`).
  - **Reserved Root Block Allocation (`-trb` / `-r`):** Checks reserved block percentage via `tune2fs`, highlighting reclaimed storage or flagging when ext4's default 5% allocation is wasting tens of gigabytes.

#### Example Audit Output:
```text
======================================================================
                STORAGE DEVICE AUDIT: /dev/sdb
======================================================================
Target Device:     /dev/sdb1 (Parent: /dev/sdb, Generic HDD, 2.0T, part)
Target Mountpoint: /mnt/storage
Target Owner:      <username>
----------------------------------------------------------------------
COMPONENT AUDIT:
  [✓] Filesystem (--format):       ext4 | UUID: 12345678-abcd-ef01-2345-6789abcdef01 | Label (-l): "storage_pool"
  [✓] Active Mount (--mount):      Mounted at /mnt/storage (-m)
  [✓] Persistence (--fstab):       Valid persistent entry found in /etc/fstab for /mnt/storage
  [✓] Permissions (--perms):       Directory exists | Owner (-u): <username> | Perms: 755
----------------------------------------------------------------------
STORAGE EFFICIENCY & ALLOCATION:
  Inodes (-irt):                   122,093,568 total (4,148 used, 122,089,420 free) [Profile: default] | Table Overhead: 29.11 GB
  Reserved Space (-trb / -r):      1.0% (18.6 GB reserved for root) [Tool Target: 1%]
----------------------------------------------------------------------
✓ AUDIT RESULT: Storage setup is complete, healthy, and persistent!
No setup actions required.
======================================================================
```

### 3. Provision a New Media Drive From Scratch
Format a drive with media inode optimizations, tune reserved space to 1%, mount to `/mnt/storage`, persist to `/etc/fstab`, and set standard `755` ownership (automatically defaults to your active user):
```bash
sudo ./drive_setup.py -d /dev/sdb1 \
  -m /mnt/storage \
  -l "storage_pool" \
  -irt largefile \
  -r 1 \
  -fmt
```
*(Passing `-fmt` automatically cascades through Format ➔ Tune ➔ Mount ➔ Fstab ➔ Permissions).*

### 4. Mount & Persist an Already Formatted Drive
If your drive already has a filesystem and data, mount it and establish persistence without reformatting:
```bash
sudo ./drive_setup.py -d /dev/sdb1 -m /mnt/storage -mnt
```
*(Cascades through Mount ➔ Fstab ➔ Permissions).*

### 5. Tune Reserved Root Blocks on an Active Ext4 Drive
Reclaim 50–100GB of wasted reserved root blocks on an existing drive (operates online, no unmount required):
```bash
sudo ./drive_setup.py -d /dev/sdb1 -trb -r 1
```

---

## ⚙️ CLI Reference

### Setup Options

| Flag | Long Option | Description | Default |
| :--- | :--- | :--- | :--- |
| `-d` | `--device` | Target block device (e.g. `/dev/sdb`, `/dev/sdb1`, `/dev/nvme1n1p1`) | *Required for setup/verify* |
| `-m` | `--mountpoint` | Target mount point directory (e.g. `/mnt/storage`) | *Required for setup* |
| `-l` | `--label` | Filesystem label (used when formatting) | None |
| `-t` | `--type` | Filesystem type (`ext4`) | `ext4` |
| `-irt` | `--inode-reserve-type` | Inode ratio profile (`largefile`: 1MB/inode, `largefile4`: 4MB/inode, `default`: 16KB/inode) | `largefile` |
| `-r` | `--reserved-percent` | Reserved root blocks percentage | `1` |
| `-u` | `--user` | Mountpoint directory owner user/group | Invoking user (`$SUDO_USER` / `$USER`) |
| `-y` | `--yes` | Non-interactive mode (skips confirmation prompts) | `False` |
| `-f` | `--force` | Force operations (override partition protection, remount) | `False` |
| `-v` | `--verbose` | Enable verbose/debug subprocess logging | `True` |
| `-q` | `--quiet` | Quiet mode: suppress debug command logs and subprocess output | `False` |
| `--color` | | Color mode: `auto` (default, TTY/NO_COLOR detected), `always`, `never` | `auto` |
| | `--json` | Output pure structured JSON payload on stdout (for AI agents & scripts) | `False` |

### Modes & Stages

| Flag | Long Option | Description |
| :--- | :--- | :--- |
| `-s` | `--scan` | **Discovery Mode:** Scan system storage devices (displays configured and unconfigured) |
| `-uo` | `--unconfigured-only` | **Filter:** Display only unconfigured / available storage devices (skips configured table) |
| `-V` | `--verify` | **Audit Mode:** Check device health, mount, fstab, and perms |
| `-fmt` | `--format` | **Stage 1:** Format drive & cascade through all subsequent stages |
| `-trb` | `--tune-reserve-block` | **Stage 2:** Tune reserved root block percentage (`tune2fs`) & cascade |
| `-mnt` | `--mount` | **Stage 3:** Mount filesystem & cascade to FSTAB and PERMS |
| `-fst` | `--fstab` | **Stage 4:** Add persistent UUID entry to `/etc/fstab` & cascade to PERMS |
| `-p` | `--perms` | **Stage 5:** Set mountpoint ownership and `755` permissions |
| `-a` | `--all` | Complete pipeline: execute stages 1 through 5 |

---

## 🤖 AI Agent & Automation Integration

`drive_setup.py` is purpose-built for both terminal-native AI agents (e.g. Antigravity, Claude Code, Cursor, terminal subagents) and traditional CI/CD / headless automation:

### 1. Pure Structured JSON Mode (`--json`)
When passing `--json`, the tool guarantees that `sys.stdout` contains **100% pure, parseable JSON** with standard 2-space indentation.
- **Human logs & subprocess traces are directed to `sys.stderr`**, keeping the JSON stream uncorrupted.
- Standard JSON schema includes `"status"`, `"mode"`, detailed component states, and actionable suggestions.
- On errors, standard structured error envelopes are emitted with remediation:
  ```json
  {
    "status": "error",
    "error_code": "E_INVALID_DEVICE",
    "message": "Target device '/dev/nonexistent' is not a valid block device on this system.",
    "remediation": "Check available block devices using './drive_setup.py --scan --json'."
  }
  ```

### 2. Automatic TTY & NO_COLOR Standard Compliance
- When running in an interactive terminal, rich ANSI colors and badges highlight status.
- When output is redirected or piped (e.g. `./drive_setup.py -s | cat` or background subshells), the tool automatically detects `sys.stdout.isatty() == False` and strips all ANSI escape codes.
- Adheres to the [`NO_COLOR`](https://no-color.org) standard: setting `NO_COLOR=1` disables color codes immediately.
- Explicit override is available via `--color=always` or `--color=never`.

### 3. Fail-Fast Non-Interactive Guardrail
- If an agent or automated pipeline runs a modifying stage without passing `-y` / `--yes`, the tool immediately aborts without hanging on stdin.
- Returns standard POSIX usage exit code **`2`** with clear instructions to pass `-y`.

### 4. Exit Code Contract
| Exit Code | Meaning | Context |
| :--- | :--- | :--- |
| `0` | **Success** | Scan completed, setup successful, or audit verified 100% healthy. |
| `1` | **Warning / Error** | Audit detected missing steps/unhealthy state, missing arguments, or command execution failure. |
| `2` | **Usage / Non-Interactive** | Operation required confirmation but executed in headless/agent shell without `-y` / `--yes`. |

### Agent Examples
```bash
# Agent discovers unconfigured storage and parses via jq
drive=$(./drive_setup.py --scan --json | jq -r '.unconfigured_devices[0].device // empty')

# Agent runs audit on discovered drive
./drive_setup.py -V -d "$drive" -m /mnt/storage --json

# Agent safely executes full provisioning non-interactively
sudo ./drive_setup.py -d "$drive" -m /mnt/storage -l "storage_pool" -fmt -y --json
```

---

## 🔒 Safety Safeguards

1. **Pre-Flight Action Evaluation:** Inspects current state and presents an upfront Action Plan Matrix before any action is executed.
2. **UUID Mount Persistence:** Avoids `/dev/sdX` instability across reboots or controller renumbering by binding to unique filesystem UUIDs.
3. **Automated Fstab Backups:** Prior to writing to `/etc/fstab`, a backup is written to `/etc/fstab.bak.<YYYYmmdd_HHMMSS>`.
4. **Data Protection Overrides:** Drives with existing filesystems or multiple partition tables cannot be formatted unless `--force` is explicitly provided.
5. **Standard Octal Permissions:** Applies `755` (`rwxr-xr-x`) to ensure non-root services (e.g., Plex, Jellyfin, Samba, NFS) can traverse and read storage directories safely.

---

## 📄 License

Open-source under the [MIT License](LICENSE) (or your preferred homelab license).

