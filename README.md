# Homelab Linux Utilities 🛠️

A collection of battle-tested, robust Linux utility tools designed for homelab administrators, home server setups, and automated Linux system maintenance. Zero external Python dependencies — pure standard library combined with standard Linux system tooling.

---

## Featured Tool: `drive_setup.py`

`drive_setup.py` is an intelligent, generic storage management and provisioning tool built to automate the end-to-end lifecycle of adding storage drives (SATA HDDs, SSDs, NVMe drives) to a Linux server or homelab node.

It replaces brittle multi-step manual processes (`mkfs`, `tune2fs`, `mkdir`, `mount`, editing `/etc/fstab`, `chown`, `chmod`) with an idempotent, cascading lifecycle pipeline equipped with pre-flight safety checks and audit modes.

### 🌟 Key Highlights

- **Idempotent Pre-Flight Action Matrix:** Evaluates current filesystem, mount, fstab, and permission states before executing anything. Skipped actions are highlighted so you never accidentally reformat or duplicate fstab entries.
- **Cascading Lifecycle Execution:** Trigger any lifecycle stage, and the tool automatically cascades through all subsequent stages:
  - `format` ➔ `tune-reserve` ➔ `mount` ➔ `fstab` ➔ `perms` (or run `setup all`)
- **Storage Space Optimization for Media Drives:**
  - **Inode Ratio Tuning (`-irt`):** Ext4 defaults to 16KB per inode, wasting tens of gigabytes of disk space on inode tables for large media/archive disks. Supports `-irt largefile` (1MB/inode) and `largefile4` (4MB/inode), reclaiming substantial usable capacity.
  - **Reserved Root Block Tuning (`-r`):** Reduces ext4's default 5% root block reservation down to 1% (or custom percentage), recovering 50–100GB+ of usable space on multi-terabyte drives. Can also be applied live to active filesystems.
- **Smart Partition Resolution:** Automatically disambiguates parent block devices (e.g., `/dev/sdb`) to child partitions (e.g., `/dev/sdb1`) when only one partition exists, preventing false "unformatted" warnings while preserving parent hardware metadata (model, serial, size).
- **Accidental Wipe Protection:** Multi-partition disks and drives with existing filesystems cannot be formatted without passing `--force` (`-f`).
- **Bulletproof Persistence:** Adds `/etc/fstab` entries by **UUID** (never volatile `/dev/sdX` device names) and creates timestamped backups (`/etc/fstab.bak.<timestamp>`) prior to any modification.
- **Dynamic Personalization & Caller Resolution:** Automatically personalizes CLI descriptions to the local machine's hostname (`socket.gethostname()`) and defaults directory ownership to the invoking caller (`$SUDO_USER` or current user), with full support for explicit user overrides (`-u / --user`).
- **Live Output Streaming & Detailed Logging:** Real-time stdout/stderr output streaming for long operations (e.g., `mkfs.ext4` inode allocation and journal creation), complete with ANSI-colored logging and a `-q / --quiet` option.
- **Safety-by-Design Architecture:** Pure read-only discovery (`scan`) and audit (`verify`) at the root level; mutating operations strictly isolated to dedicated subcommands (`setup` and `apm`).

---

## 📋 Requirements

- Linux (tested on Debian / Ubuntu / Proxmox / modern Linux distributions)
- Python 3.7+ (uses only Python Standard Library)
- Standard system utilities: `util-linux` (`lsblk`, `blkid`, `findmnt`, `mount`, `umount`), `e2fsprogs` (`mkfs.ext4`, `tune2fs`)
- Root privileges (`sudo`) for provisioning and power management operations (discovery scanning and audit verifying can run unprivileged)

---

## 🚀 Quick Start & Usage Examples

Make the script executable:
```bash
chmod +x drive_setup.py
```

### 1. Discovery Mode (Default / `-s`, `-uo` / `--unconfigured-only`)

Running `./drive_setup.py` without arguments automatically performs a complete discovery scan across all host block devices without modifying anything.

By default, the scan presents two clean, structured sections:
1. **Configured & Active Storage Devices:** A compact audit table showing active drives with their filesystem, volume label, mountpoint, `/etc/fstab` persistence, directory owner/permissions, and ext4 efficiency metrics.
2. **Unconfigured / Available Storage Devices:** Identifies raw, unpartitioned, unmounted, or orphaned storage, complete with tailored setup command recipes.

```bash
# Default storage inventory (both configured and unconfigured)
./drive_setup.py
# or explicitly with -s / --scan:
./drive_setup.py -s

# Display only unconfigured / available devices (skips configured table)
./drive_setup.py -uo

# Pure JSON discovery for AI agents or automation:
./drive_setup.py -j
```

Example scan output:
```text
=======================================================================================================================================
                                             CONFIGURED & ACTIVE STORAGE DEVICES
=======================================================================================================================================
DEVICE       SIZE    FS (LABEL)           MOUNTPOINT             FSTAB   APM          OWNER (PERMS)      INODES (OVERHEAD)        RESERVED SPACE
---------------------------------------------------------------------------------------------------------------------------------------
/dev/sdb1    1.8T    ext4 ("storage_pool  /mnt/storage           [✓]     128 (Bal)    <user> (755)       122.1M (29.1 GB ovh)     18.6 GB (1.0%)
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

#### What Discovery Checks:
- **Device Topology:** Probes all attached SATA, SAS, USB, and NVMe drives and partitions via `lsblk`.
- **Partition & Filesystem State:** Identifies whether drives are raw/unpartitioned, formatted, or have missing filesystems.
- **Mount & Usage Status:** Distinguishes between active, mounted filesystems and orphaned or unmounted storage.
- **Active Drive Health:** Extracts mountpoint directory ownership/permissions and ext4 storage efficiency (total inodes, profiles, and root reservation percentage) directly into the summary table.
- **Smart Recommendations:** Automatically generates tailored, ready-to-run setup commands (pre-filling suggested partitions, labels, and mount points) for any unconfigured drive detected.

---

### 2. Device Audit Mode (`-V` / `--verify`)

Performs a deep, non-destructive health and configuration compliance check on a specific device and target mount point.

```bash
./drive_setup.py -V -d /dev/sdb1 -m /mnt/storage
# or with JSON output:
./drive_setup.py -V -d /dev/sdb1 -m /mnt/storage -j
```

#### What `-V / --verify` Checks:
- **Smart Partition Resolution:** Disambiguates parent disks (e.g. `/dev/sdb`) to child partitions (e.g. `/dev/sdb1`), preventing false "unformatted" warnings while verifying hardware model and size.
- **Filesystem Integrity:** Queries `blkid` for filesystem type, volume UUID, and assigned label.
- **Active Mount Verification:** Verifies active mounts in `/proc/mounts`, detecting if the drive is unmounted or mounted to the wrong directory.
- **Persistence Verification:** Validates `/etc/fstab` entries to ensure the device has a permanent, UUID-bound mount point that will survive reboot.
- **Directory Permissions:** Confirms the mount directory exists, verifies ownership against the expected user (`-u`), and validates standard `755` permissions.
- **Storage Efficiency & Geometry (ext4):**
  - **Inode Allocation & Profile (`-irt`):** Inspects total vs. free inodes, calculates exact inode table overhead in MB/GB, and identifies active profile (`largefile`, `largefile4`, `default`).
  - **Reserved Root Block Allocation (`-r`):** Checks reserved block percentage via `tune2fs`, highlighting reclaimed storage or flagging when ext4's default 5% allocation is wasting tens of gigabytes.

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
  [✓] Filesystem (format):         ext4 | UUID: 12345678-abcd-ef01-2345-6789abcdef01 | Label (-l): "storage_pool"
  [✓] Active Mount (mount):        Mounted at /mnt/storage (-m)
  [✓] Persistence (fstab):         Valid persistent entry found in /etc/fstab for /mnt/storage
  [✓] Permissions (perms):         Directory exists | Owner (-u): <username> | Perms: 755
----------------------------------------------------------------------
STORAGE EFFICIENCY & ALLOCATION:
  Inodes (-irt):                   122,093,568 total (4,148 used, 122,089,420 free) [Profile: default] | Table Overhead: 29.11 GB
  Reserved Space (-r):             1.0% (18.6 GB reserved for root) [Tool Target: 1%]
----------------------------------------------------------------------
✓ AUDIT RESULT: Storage setup is complete, healthy, and persistent!
No setup actions required.
======================================================================
```

### 3. Provision a New Media Drive From Scratch (`setup all`)
Format a drive with media inode optimizations, tune reserved space to 1%, mount to `/mnt/storage`, persist to `/etc/fstab`, and set standard `755` ownership (automatically defaults to your active user):
```bash
sudo ./drive_setup.py setup all \
  -d /dev/sdb1 \
  -m /mnt/storage \
  -l "storage_pool" \
  -irt largefile \
  -r 1
```

### 4. Format & Cascade (`setup format`)
Format a partition with `largefile` inodes and let it cascade into mount, fstab, and permissions:
```bash
sudo ./drive_setup.py setup format -d /dev/sdb1 -m /mnt/storage -l "storage_pool" -irt largefile
```
*(Pass `--no-cascade` if you only want to format the partition without mounting).*

### 5. Mount & Persist an Already Formatted Drive (`setup mount`)
If your drive already has a filesystem and data, mount it and establish persistence without reformatting:
```bash
sudo ./drive_setup.py setup mount -d /dev/sdb1 -m /mnt/storage
```
*(Cascades through Mount ➔ Fstab ➔ Permissions).*

### 6. Tune Reserved Root Blocks on an Active Ext4 Drive (`setup tune-reserve`)
Reclaim 50–100GB of wasted reserved root blocks on an existing drive (operates online, no unmount required):
```bash
sudo ./drive_setup.py setup tune-reserve -d /dev/sdb1 -r 1
```

### 7. Tune HDD Power Management (`apm`) & Emit Observability Breadcrumb
Adjust drive power management profile with persistent udev rules and automatic Loki telemetry:
```bash
sudo ./drive_setup.py apm -d /dev/sdb -l 128 -r
```

---

## ⚙️ CLI Reference

### 1. Global Options (Available Across Root and All Subcommands)

| Flag | Long Option | Description | Default |
| :--- | :--- | :--- | :--- |
| `-j` | `--json` | Output pure structured JSON payload on stdout (for AI agents & scripts) | `False` |
| `-v` | `--verbose` | Enable verbose/debug subprocess logging | `True` |
| `-q` | `--quiet` | Quiet mode: suppress debug command logs and subprocess output | `False` |
| `-c` | `--color` | Color mode: `auto` (default, TTY/NO_COLOR detected), `always`, `never` | `auto` |
| `-N` | `--no-telemetry` | Explicitly disable sending event breadcrumbs to Alloy/Loki | `False` |
| `-U` | `--alloy-url` | HTTP endpoint for Grafana Alloy / Loki push | `http://127.0.0.1:9999` (or `$ALLOY_URL`) |

---

### 2. Root Audit & Discovery Options

| Flag | Long Option | Description | Default |
| :--- | :--- | :--- | :--- |
| `-s` | `--scan` | **Discovery Scan:** Scan system storage devices (displays configured and unconfigured; **default action**) | `True` if no args |
| `-uo` | `--unconfigured-only` | **Scan Filter:** Display only unconfigured / available storage devices | `False` |
| `-V` | `--verify` | **Audit Mode:** Check device health, active mount, fstab persistence, and directory ownership | `False` |
| `-d` | `--device` | Target block device or partition for verification (e.g. `-d /dev/sdb1`) | Required for `-V` |
| `-m` | `--mountpoint` | Target mount point directory for verification (e.g. `-m /mnt/storage`) | Optional |
| `-u` | `--user` | Expected mountpoint directory owner for verification | Invoking user |
| | `--reload-udev` | Standalone reload and trigger of udev rules via `udevadm` | `False` |

---

### 3. Dedicated Power Management Subcommand: `apm`

```bash
sudo ./drive_setup.py apm -d <device> -l <level> [-n] [-M {drive,uuid}] [-r] [-y]
```

| Flag | Long Option | Description | Default |
| :--- | :--- | :--- | :--- |
| `-d` | `--device` | Target block device or child partition (e.g. `/dev/sdb` or `/dev/sdb1`) | *Required* |
| `-l` | `--level` | Target APM level (`128`: balanced, `254`: performance/no-parking, `off`/`255`: disabled) | *Required* |
| `-n` | `--no-persist` | Disable writing persistent udev rule (transient runtime modification only) | `False` |
| `-M` | `--match` | Udev matching strategy for persistence (`drive` or `uuid`) | `drive` |
| `-r` | `--reload-udev` | Trigger `udevadm control --reload-rules && udevadm trigger` immediately | `False` |
| `-y` | `--yes` | Skip interactive confirmation prompts | `False` |
| `-f` | `--force` | Force APM update even if drive reports existing level | `False` |

---

### 4. Dedicated Storage Provisioning Subcommand: `setup` (alias: `provision`)

```bash
sudo ./drive_setup.py setup <stage> [options]
```

#### Stage Summary & Arguments:

| Stage | Required Flags | Optional Flags | Description & Cascading Flow |
| :--- | :--- | :--- | :--- |
| **`all`** | `-d <dev>`, `-m <path>`, `-l <label>` | `-t`, `-irt`, `-r`, `-u`, `-y`, `-f` | Complete pipeline: Format ➔ Tune ➔ Mount ➔ Fstab ➔ Perms |
| **`format`** | `-d <dev>`, `-l <label>` | `-m`, `-u`, `-t`, `-irt`, `-r`, `--no-cascade` | Format filesystem; cascades to tune/mount/fstab/perms if `-m` provided |
| **`tune-reserve`**| `-d <dev>`, `-r <pct>` | `-m`, `--no-cascade` | Tune ext4 reserved root blocks (online/offline); cascades if `-m` provided |
| **`mount`** | `-d <dev>`, `-m <path>` | `-u`, `--no-cascade` | Mount drive and cascade through Fstab and Perms |
| **`fstab`** | `-d <dev>`, `-m <path>` | `-u`, `--no-cascade` | Configure `/etc/fstab` persistent UUID entry and cascade to Perms |
| **`perms`** | `-m <path>` | `-d <dev>`, `-u <user>` | Set mount point ownership and standard `755` permissions |

---

## ⚡ HDD Power Management (APM) & Udev Persistence

ATA Advanced Power Management (APM) controls how aggressively rotational hard drives park their heads and enter low-power idle states. In a homelab, APM represents a fundamental trade-off between **mechanical head wear** and **operating temperatures**:

* **APM 254 (Performance: No Head Parking / No Spindown)**:
  - Completely disables head parking and spindown. The heads remain flying over the platters with 0 wake latency and zero SMART `Load_Cycle_Count` incrementing.
  - **Thermal Penalty**: The voice coil and pre-amplifier electronics remain fully energized, continuously drawing 2–4W more power. In a drive cage with restricted airflow, this can push drive temperatures from **40°C up to 49°C+**, which accelerates motor bearing wear.
* **APM 128 (Balanced: Idle Head Parking / No Spindown - Recommended for Warm Drives)**:
  - Spindown is disabled (the spindle motor never stops spinning), but heads are permitted to park to the ramp during prolonged idle according to internal firmware timers.
  - **Thermal Benefit**: Runs **5°C to 8°C cooler**, keeping operating temperatures within the safe 35°C–42°C longevity window.
* **APM 1–127 (Power Saving: Spindown Enabled)**:
  - Aggressive power saving permitting spindle spindown. Not recommended for 24/7 NAS or media drives due to spin-up latency and spindle motor start/stop cycles.

### 🛡️ Automated Udev Persistence (`/etc/udev/rules.d/69-hdparm-apm.rules`)
By default, running `sudo ./drive_setup.py apm -d <device> -l <level>` automatically creates or updates persistent rules in `/etc/udev/rules.d/69-hdparm-apm.rules` so settings survive server reboots:

* **Default-On Persistence**: Automatically writes the rule unless you explicitly pass `-n / --no-persist` for transient runtime sessions.
* **Udev Matching Strategies (`-M / --match {drive,uuid}`)**:
  - **`-M drive` (Default)**: Generates a kernel device name rule (`KERNEL=="sdX"`). As a safety guardrail, `drive_setup.py` checks that the drive has an active entry in `/etc/fstab` before writing this rule. If missing, it fails fast to protect you from drive-letter swap issues across reboots.
  - **`-M uuid`**: Binds the rule to the filesystem UUID (`ENV{ID_FS_UUID}=="<UUID>"`), completely immune to drive-letter shifts across USB ports or SATA controllers.
* **Decoupled Udev Reloading (`-r / --reload-udev`)**:
  - Pass `-r / --reload-udev` with `apm` to trigger `udevadm control --reload-rules && udevadm trigger` immediately.
  - Can also be executed standalone: `sudo ./drive_setup.py apm -r` or `sudo ./drive_setup.py --reload-udev`.
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
  "apm_status": "Level 128 (Balanced: Idle Head Parking / No Spindown)",
  "fstype": "ext4",
  "label": "The_Archives",
  "mountpoint": "/mnt/TheArchives",
  "ext4_root_reserved_pct": 1.0,
  "ext4_root_reserved_bytes": 19998441472,
  "ext4_root_reserved_gb": 18.63,
  "ext4_inode_table_overhead_bytes": 31258288128,
  "ext4_inode_table_overhead_gb": 29.11,
  "ext4_inode_ratio_profile": "default"
}
```

* **`ext4_root_reserved_pct`**: Root block reservation percentage (`1.0%` homelab media standard vs `5.0%` OS default).
* **`ext4_root_reserved_bytes`**: Exact raw byte count reserved exclusively for root (for exact LogQL calculations).
* **`ext4_root_reserved_gb`**: Convenient rounded gigabytes reserved for root.
* **`ext4_inode_table_overhead_bytes`**: Exact disk bytes allocated to inode tables.
* **`ext4_inode_table_overhead_gb`**: Convenient rounded gigabytes allocated to inode tables.
* **`ext4_inode_ratio_profile`**: Inode density profile (`largefile`, `largefile4`, or `default`).

> [!NOTE]
> **Telemetry Boundary (Prometheus vs. Loki)**:
> Dynamic time-series metrics (total/used/free inodes, live filesystem free bytes, real-time spindle states, and SMART health) are intentionally omitted from this event telemetry because they are continuously scraped by `node_exporter` and `smartctl_exporter` into Prometheus/Mimir. `drive_setup.py` strictly audits static storage architecture, superblock ext4 allocation geometry, and configured hardware APM policy.

#### Telemetry Event & Action Taxonomy

| Event Domain (`event`) | Action (`action`) | Trigger / Operation | Indexed Labels |
| :--- | :--- | :--- | :--- |
| `storage_audit` | `scan` | System-wide block device discovery (`./drive_setup.py` / `-s`) | `source`, `event`, `action`, `level`, `device` |
| `storage_audit` | `verify` | Mountpoint and persistence audit (`-V / --verify`) | `source`, `event`, `action`, `level`, `device` |
| `hardware_tune` | `apm_tune` | ATA APM power level tuned (`apm -l <level>`) | `source`, `event`, `action`, `level`, `device` |
| `storage_provision` | `format` | Ext4 filesystem creation (`setup format`) | `source`, `event`, `action`, `level`, `device` |
| `storage_provision` | `tune_reserve` | Root reserved block tuning (`setup tune-reserve`) | `source`, `event`, `action`, `level`, `device` |
| `storage_provision` | `mount` | Filesystem mount (`setup mount`) | `source`, `event`, `action`, `level`, `device` |
| `storage_provision` | `fstab` | `/etc/fstab` persistence update (`setup fstab`) | `source`, `event`, `action`, `level`, `device` |
| `storage_provision` | `permissions` | Directory chown / chmod (`setup perms`) | `source`, `event`, `action`, `level`, `device` |
| `storage_provision` | `setup_all` | End-to-end cascading setup pipeline (`setup all`) | `source`, `event`, `action`, `level`, `device` |

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

### 1. Pure Structured JSON Mode (`-j / --json`)
When passing `-j` / `--json`, the tool guarantees that `sys.stdout` contains **100% pure, parseable JSON** with standard 2-space indentation.
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
drive=$(./drive_setup.py -j | jq -r '.unconfigured_devices[0].device // empty')

# Agent runs audit on discovered drive
./drive_setup.py -V -d "$drive" -m /mnt/storage -j

# Agent safely executes full provisioning non-interactively
sudo ./drive_setup.py setup all -d "$drive" -m /mnt/storage -l "storage_pool" -y -j
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

