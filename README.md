# Netbook Architecture & Applications

**Hardware:** Old i386 Xubuntu 18.04 LTS on netbook (limited CPU, RAM, disk)  
**Primary Use:** Photo display device + archive organization hub  
**Storage:** 156GB root partition (expanded 2026-09-19), external 360GB archive drive

---

## Three-App Ecosystem

### 1. **digital_frame.sh** — Photo Display Service

**Purpose:** Continuous slideshow of curated photos on desktop/monitor.

**Architecture:**
- `feh` image viewer (lightweight, i386-compatible)
- Bash orchestration script: cycle through folder, apply effects, display
- Cron-scheduled restart on boot
- DPMS disabled (prevents display sleep)
- Keybindings: navigation (h/j/k/l), pause/resume, delete current

**Data Flow:**
```
photo folder → feh loop → display
     ↓
  Syncthing (config backup)
```

**Decision (ADR):**
- **Why feh?** Minimal resource overhead vs. galleries (Immich, Geeqie)
- **Why local folder?** No network dependency; responsive on i386
- **Why Syncthing backup?** Config portable; can redeploy to another netbook/device

**Goal:** Reliable, always-on slideshow with <50MB RAM footprint.

**Current Status:** ✅ Working (digital_frame.sh + digital_frame_v*.tar.gz backups)

---

### 2. **Syncthing** — Family Photo Backup Hub

**Purpose:** Central backup destination for family smartphones' photo galleries.

**Architecture:**
- Syncthing daemon (systemd service)
- **Primary flow:** Family smartphones (iOS/Android) → netbook `/media/backup/sync_data/`
- **Secondary backup:** netbook `/home` → Windows PC `C:\Users\lw_na\Syncthing\` (disaster recovery)
- Config: ignore list (cache, dbus, gnupg, snapshots)

**Data Flow:**
```
Family phones (photos) → Syncthing → netbook:/media/backup/sync_data/
                                           ↓
                                    (secondary backup)
                                           ↓
                                    Windows PC backup
```

**Decision (ADR):**
- **Why Syncthing?** Lightweight, works on old hardware; family members already familiar with it
- **Why netbook as hub?** Central always-on backup destination for household devices
- **Why Windows secondary?** Redundancy—if netbook fails, family photos still on Windows

**Goal:** 
- Reliable backup of family smartphones' photo galleries (primary)
- Preserve netbook config + digital_frame setup (secondary)

**Current Status:** ✅ Working (backup folder verified post-expansion)

---

### 3. **dupe-sweep mvp2-remote** — Archive Deduplication & Organization

**Purpose:** Browse, deduplicate, and organize 360GB photo archive without copying to PC.

**Architecture:**
- Python Flask web server (runs on Windows PC)
- SSH/SFTP connection to netbook (paramiko)
- Remote scan over network: perceptual hashing (dHash) + time clustering
- Optimized: reads EXIF thumbnails only (~19x faster than full-image download)
- Web UI: browse → scan → review duplicates → delete/archive

**Data Flow:**
```
netbook:/media/backup/archive (360GB)
    ↓ (SSH/SFTP)
Windows PC: mvp2-remote (scan, hash, score)
    ↓ (Flask web UI @ localhost:5036)
Browser: review, delete, organize
    ↓ (SFTP move)
netbook: files moved to _to_delete or archived
```

**Decision (ADR):**
- **Why not copy to PC?** 360GB transfer = weeks + disk space waste
- **Why SSH/SFTP?** Netbook already has sshd; no new server install needed
- **Why hashing on PC?** i386 netbook can't do image processing efficiently
- **Why Flask UI?** Accessible from any device on LAN; browser-based review

**Goal:** Organize archive into deduped + categorized folders; reclaim wasted space.

**Current Status:** ⏳ Ready to deploy (Python app exists; not yet run against netbook)

---

## Data Organization

```
netbook /media/backup/
├── sync_data/           (Syncthing active folder — family phone backups + netbook /home)
│   ├── phone-user1/     (photos from family member 1)
│   ├── phone-user2/     (photos from family member 2)
│   └── netbook-home/    (config backups, digital_frame scripts)
│
├── archive/             (dupe-sweep target — stable, deduplicated photos)
│   ├── sculptures/      (organized by type after dedup)
│   ├── exhibitions/
│   ├── classes/
│   ├── _to_delete/      (soft-delete: files moved here, not hard-deleted)
│   └── ...
│
└── (raw incoming: 360GB undeduplicated Dalila artwork archive)

Windows PC /mnt/e/dev/
├── Syncthing/           (backup of netbook /media/backup/sync_data/ — family photos + config)
├── Site_Dalila/         (website git repo — will pull best photos from archive)
└── dupe-sweep/          (mvp2-remote scripts)
```

---

## Constraints & Assumptions

| Constraint | Impact | Mitigation |
|---|---|---|
| **Old i386 hardware** | Limited CPU/RAM | Single-threaded apps; isolate heavy work to Windows |
| **360GB archive** | Can't copy to PC | Use SSH/SFTP; scan over network |
| **No PHP support** | WordPress not viable | Use static site (11ty) for Site_Dalila |
| **External USB drive** | Fails if unplugged | Archive folder always on netbook external; PC has backup |
| **Syncthing one-way** | Manual recovery needed | Restore from Windows if netbook fails |

---

## Interaction Map

```
Family Phones (iOS/Android)
         │ (WiFi)
         ↓ Syncthing
┌─────────────────────────────────────────────────────┐
│                  Netbook (i386)                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  /media/backup/sync_data/ ←─ Family phone backups  │
│  (Syncthing hub: photos from family)               │
│         ↓                                           │
│  /media/backup/archive/ (360GB dupe-sweep target) │
│  - Deduplicate over SSH/SFTP                       │
│  - Organize into folders                          │
│  - Move deletions to _to_delete                    │
│         ↓                                           │
│  digital_frame.sh displays curated photos          │
│  (pulls from organized archive)                    │
│                                                     │
│  /home (config) ──→ Syncthing (backup to PC)       │
│                                                     │
└─────────────────────────────────────────────────────┘
         ↑                              ↑
         │ sshd                         │ Syncthing (backup)
         │                              │
┌────────────────────────────────────────────────────┐
│              Windows PC                            │
├────────────────────────────────────────────────────┤
│                                                    │
│ dupe-sweep mvp2-remote (Flask server)              │
│ └─ Browse, scan, review, delete (web UI)           │
│                                                    │
│ Syncthing: /mnt/e/dev/Syncthing/                  │
│ ├─ family phone backups (redundancy)              │
│ └─ netbook /home config (disaster recovery)       │
│                                                    │
└────────────────────────────────────────────────────┘
```

---

## SSH Access to Netbook

### Setup
Netbook runs `sshd` (required for dupe-sweep mvp2-remote). SSH key-based auth is already configured.

**Netbook SSH details:**
- **Host:** netbook IP on local network (e.g., `192.168.4.36`)
- **User:** `netbook`
- **Port:** 22 (default)
- **Auth:** SSH key-based (configured)

### Connect via SSH
```bash
ssh netbook@192.168.4.36
```

Replace `192.168.4.36` with actual netbook IP on your network. Find it via:
```bash
arp -a | grep -i netbook    # macOS/Linux
ipconfig /all               # Windows
```

### SFTP (for file operations)
```bash
sftp netbook@192.168.4.36
# or via dupe-sweep mvp2-remote (handles SFTP automatically)
```

### How SSH is used:

1. **dupe-sweep mvp2-remote:** SSH/SFTP to access `/media/backup/archive/` and perform remote scans + deletes from Windows PC
2. **Manual config backup:** `scp` to pull `/home` backups during disaster recovery
3. **Remote troubleshooting:** SSH into netbook to debug Syncthing, digital_frame, or network issues

---

## Next Steps

1. ✅ **Understand current state** (this doc)
2. ⏳ **Set up SSH access** (above; needed for dupe-sweep)
3. ⏳ **Set up dupe-sweep SSH connection** (`--host netbook-ip`)
4. ⏳ **Test scan + review workflow** (browser UI)
5. ⏳ **Organize archive** (dedup + category folders)

---

## Files in This Repo

- `README.md` — This overview
- `adr/` — Architecture Decision Records (one per major decision)
- `backup-scripts/` — Netbook backup/restore procedures
- `deployment/` — Setup scripts for digital_frame, Syncthing, dupe-sweep

