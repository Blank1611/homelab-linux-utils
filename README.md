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

### 6. Tune HDD Power Management (APM) & Emit Observability Breadcrumb
Adjust drive power management profile while sending an audit event directly into your Grafana Alloy / Loki observability stack:
```bash
sudo ./drive_setup.py -d /dev/sdb --tune-apm 128
```

---

## ⚙️ CLI Reference

### Setup Options

| Flag | Long Option | Description | Default |
| :--- | :--- | :--- | :--- |
| `-d` | `--device` | Target block device (e.g. `/dev/sdb`, `/dev/sdb1`, `/dev/nvme1n1p1`) | *Required for setup/verify/apm* |
| `-m` | `--mountpoint` | Target mount point directory (e.g. `/mnt/storage`) | *Required for setup* |
| `-l` | `--label` | Filesystem label (used when formatting) | None |
| `-t` | `--type` | Filesystem type (`ext4`) | `ext4` |
| `-irt` | `--inode-reserve-type` | Inode ratio profile (`largefile`: 1MB/inode, `largefile4`: 4MB/inode, `default`: 16KB/inode) | `largefile` |
| `-r` | `--reserved-percent` | Reserved root blocks percentage | `1` |
| `-u` | `--user` | Mountpoint directory owner user/group | Invoking user (`$SUDO_USER` / `$USER`) |
| | `--tune-apm` | Tune ATA APM level on rotational HDDs (`128`, `254`, `off`) | None |
| | `--no-persist-apm` | Disable writing persistent udev rule when tuning APM (runtime-only modification) | `False` |
| | `--udev-match` | Udev matching strategy for APM persistence (`drive` or `uuid`) | `drive` |
| | `--reload-udev` | Reload and trigger udev rules (standalone or with `--tune-apm`) | `False` |
| | `--alloy-url` | HTTP endpoint for Grafana Alloy / Loki push | `http://127.0.0.1:9999` (or `$ALLOY_URL`) |
| | `--no-telemetry` | Explicitly disable sending event breadcrumbs to Alloy/Loki | `False` |
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
| | `--tune-apm` | **Hardware Tuning:** Adjust ATA APM level on rotational hard drives & emit event |
| `-fmt` | `--format` | **Stage 1:** Format drive & cascade through all subsequent stages |
| `-trb` | `--tune-reserve-block` | **Stage 2:** Tune reserved root block percentage (`tune2fs`) & cascade |
| `-mnt` | `--mount` | **Stage 3:** Mount filesystem & cascade to FSTAB and PERMS |
| `-fst` | `--fstab` | **Stage 4:** Add persistent UUID entry to `/etc/fstab` & cascade to PERMS |
| `-p` | `--perms` | **Stage 5:** Set mountpoint ownership and `755` permissions |
| `-a` | `--all` | Complete pipeline: execute stages 1 through 5 |

---

## ⚡ HDD Power Management (APM) & Udev Persistence

ATA Advanced Power Management (APM) controls how aggressively rotational hard drives park their heads and enter low-power idle states. In a homelab, APM represents a fundamental trade-off between **mechanical head wear** and **operating temperatures**:

* **APM 254 (Maximum Performance / Heads Loaded)**:
  - Completely disables head parking and spindown. The heads remain flying over the platters with 0 wake latency and zero SMART `Load_Cycle_Count` incrementing.
  - **Thermal Penalty**: The voice coil and pre-amplifier electronics remain fully energized, continuously drawing 2–4W more power. In a drive cage with restricted airflow, this can push drive temperatures from **40°C up to 49°C+**, which accelerates motor bearing wear.
* **APM 128 (Standard Homelab Idle - Recommended for Warm Drives)**:
  - Spindown is disabled (the spindle motor never stops spinning), but heads are permitted to park to the ramp during prolonged idle according to internal firmware timers.
  - **Thermal Benefit**: Runs **5°C to 8°C cooler**, keeping operating temperatures within the safe 35°C–42°C longevity window.
* **APM 1–127**:
  - Aggressive power saving permitting spindle spindown. Not recommended for 24/7 NAS or media drives due to spin-up latency and spindle motor start/stop cycles.

### 🛡️ Automated Udev Persistence (`/etc/udev/rules.d/69-hdparm-apm.rules`)
By default, running `--tune-apm <LEVEL>` automatically creates or updates persistent rules in `/etc/udev/rules.d/69-hdparm-apm.rules` so settings survive server reboots:

* **Default-On Persistence**: Automatically writes the rule unless you explicitly pass `--no-persist-apm` for transient runtime sessions.
* **Udev Matching Strategies (`--udev-match {drive,uuid}`)**:
  - **`--udev-match drive` (Default)**: Generates a kernel device name rule (`KERNEL=="sdX"`). As a safety guardrail, `drive_setup.py` checks that the drive has an active entry in `/etc/fstab` before writing this rule. If missing, it fails fast to protect you from drive-letter swap issues across reboots.
  - **`--udev-match uuid`**: Binds the rule to the filesystem UUID (`ENV{ID_FS_UUID}=="<UUID>"`), completely immune to drive-letter shifts across USB ports or SATA controllers.
* **Decoupled Udev Reloading (`--reload-udev`)**:
  - Pass `--reload-udev` to trigger `udevadm control --reload-rules && udevadm trigger` immediately.
  - In interactive mode, prompts whether to reload now (`[Y/n]`).
  - In non-interactive or JSON mode, emits a pending notice with the exact reload command.
  - Can also be executed standalone: `sudo ./drive_setup.py --reload-udev`.
* **USB SAT Passthrough Robustness**: External USB enclosures often output `SG_IO: bad/missing sense data` on `stderr` when receiving ATA commands. `drive_setup.py` transparently absorbs these bridge warnings as long as the drive controller confirms `APM_level`.

---

## 📡 Observability & Telemetry Gateway (Grafana Alloy & Loki)

`drive_setup.py` includes a decoupled **`BreadcrumbPublisher`** gateway that sends structured JSON operational breadcrumbs to **Grafana Alloy** (`loki.source.api`) / Loki.

Whenever an operator changes drive power management via `--tune-apm`, audits a drive via `--verify`, or discovers storage via `--scan`, structured observation breadcrumbs are pushed over HTTP:

### 1. Storage Observation Metrics (Ext4 & Hardware)
Observation breadcrumbs emitted during `--verify` and `--scan` stream self-documenting efficiency and hardware metrics labeled by `device="sdX"`:

```json
{
  "event": "storage_audit",
  "action": "verify",
  "device": "sdb1",
  "parent_disk": "sdb",
  "model": "WDC WD20SDRM-59A4DS1",
  "size": "1.8T",
  "is_rotational": true,
  "apm_level": 128,
  "apm_status": "Level 128 (Standard Idle)",
  "fstype": "ext4",
  "label": "The_Archives",
  "mountpoint": "/mnt/TheArchives",
  "ext4_root_reserved_pct": 1.0,
  "ext4_root_reserved_gb": 18.63,
  "ext4_reclaimable_space_gb": 0.0,
  "ext4_inode_table_overhead_gb": 29.11,
  "ext4_inode_ratio_profile": "default"
}
```

* **`ext4_root_reserved_pct`**: Root block reservation percentage (`1.0%` homelab media standard vs `5.0%` OS default).
* **`ext4_root_reserved_gb`**: Exact capacity reserved exclusively for root.
* **`ext4_reclaimable_space_gb`**: Storage recoverable immediately by tuning reserved root blocks to 1% via `-trb -r 1`.
* **`ext4_inode_table_overhead_gb`**: Disk space allocated to inode tables.
* **`ext4_inode_ratio_profile`**: Inode density profile (`largefile`, `largefile4`, or `default`).

### 2. Alloy Configuration (`config.river`)
Declare a `loki.source.api` block in Alloy that feeds into your existing `loki.write` block:
```river
loki.source.api "storage_hooks" {
  http {
    listen_address = "0.0.0.0"
    listen_port    = 9999
  }
  forward_to = [loki.write.local_loki.receiver]
  labels = {
    source = "homelab-drive-setup",
    job    = "storage_ops",
  }
}
```

### 3. Docker Compose Port Mapping
Ensure Alloy's ingest port is mapped securely to the host:
```yaml
services:
  alloy:
    image: grafana/alloy:latest
    ports:
      - "127.0.0.1:9999:9999"
```

### 4. Grafana Dashboard & LogQL Queries
* **Vertical Annotation Markers**: Over panels tracking **Drive Temperature vs Head Parking**:
  - **LogQL**: `{source="homelab-drive-setup", action="apm_tune"}`
  - **Tooltip**: `APM tuned for {{device}}: {{old_apm}} → {{new_apm}}`
* **Storage Efficiency Audit Queries**:
  - **LogQL**: `{source="homelab-drive-setup", action="storage_audit"}`

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

