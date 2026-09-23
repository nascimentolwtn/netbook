# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **three-app ecosystem** for photo management on an old i386 netbook (Xubuntu 18.04 LTS):

1. **digital_frame.sh** — Continuous photo slideshow display (feh-based, ~50MB footprint)
2. **Syncthing** — Family phone backup hub (systemd service, syncs to `/media/backup/sync_data/`)
3. **dupe-sweep mvp2-remote** — Archive deduplication tool (Flask web server on Windows PC, scans via SSH/SFTP)

See `/README.md` for full architecture and design decisions.

## Repository Structure

```
netbook/
├── digital_frame/        Git history of slideshow app versions (12 commits)
│   ├── digital_frame.sh  Current production script
│   └── README.md         Usage, keybindings, configuration
├── syncthing/            Config backup from netbook
│   ├── config.xml        Folder/device setup, sync settings
│   ├── cert.pem, key.pem TLS certificates
│   └── README.md         Folder mappings, secondary backup info
├── README.md             Main architecture + ADR references
└── CLAUDE.md             This file
```

## SSH Access to Netbook

All three apps run on the netbook. SSH is used for:
- **dupe-sweep** — remote archive scanning via SFTP
- **Manual troubleshooting** — debug Syncthing, digital_frame, network issues

```bash
ssh netbook@192.168.4.36
```

(SSH key auth already configured; no `-i` flag needed. Replace IP with actual netbook IP if on different network.)

## Key Files & Their Purpose

| File | Purpose |
|------|---------|
| `digital_frame/digital_frame.sh` | Production slideshow script (v19, 769 lines). Uses weighted folder selection, pre-scaled image queue, crash logging. |
| `syncthing/config.xml` | 4 shared folders (Photos_Aline/LW/Lais/Paty), 4 devices, web UI on port 8384. |
| `/README.md` | Architecture decisions, data flow, constraints, interaction map. Start here. |

## Git History

**digital_frame versions (12 commits, Aug 27 → Sep 6, 2026):**
- Captures evolution from basic slideshow → pause/resume → caching → crash logging → DPMS management
- Each commit's date and message reflects the actual version development timeline
- Useful for understanding design trade-offs (e.g., why caching was added, how arg-length issues were fixed)

View history:
```bash
git log --reverse digital_frame/
```

## Backup & Configuration

**What's backed up in this repo:**
- `digital_frame.sh` with full version history
- Syncthing config + TLS certificates (device identity, folder mappings)

**What's NOT in this repo:**
- Actual photo files (live on netbook external HDD or Windows PC)
- Syncthing database (regenerated at runtime)
- dupe-sweep source (runs on Windows PC, access via `/mnt/e/dev/dupe-sweep/`)

## Next Steps / Common Tasks

- **Deploy digital_frame to another netbook** — copy `digital_frame/digital_frame.sh` + set `photo_root` variable
- **Restore Syncthing config** — place `syncthing/config.xml` in `~/.config/syncthing/` on netbook, restore certs
- **Debug photo rotation** — check `~/.cache/digitalframe_crashes.log` or breadcrumbs log
- **Adjust slideshow timing** — edit `delay` (seconds between photos) or `queue_cap` (lookahead size) in script

## Architecture Constraints

| Constraint | Impact | Mitigation |
|---|---|---|
| Old i386 hardware (limited CPU/RAM) | Single-threaded; heavy work isolated to Windows | Image pre-scaling in background queue |
| 360GB archive on netbook | Can't copy to PC for processing | SSH/SFTP + perceptual hashing on Windows |
| External USB drive | Fails if unplugged | PC has redundant backup (Syncthing config only, not files) |

See `/README.md` for full constraint table.

## References

- **Main README:** `/README.md` — architecture, ADRs, data flow, interaction map
- **digital_frame README:** `digital_frame/README.md` — slideshow usage & configuration
- **Syncthing README:** `syncthing/README.md` — folder/device setup, secondary backup
- **Syncthing docs:** https://docs.syncthing.net/
- **dupe-sweep:** Runs on Windows PC at `/mnt/e/dev/dupe-sweep/` (not in this repo)
- **alexa-talk-pal CLAUDE.md:** `alexa-talk-pal/CLAUDE.md` — a separate Alexa-skill relay project
  living in this same repo, not part of the three-app photo ecosystem above; deploy steps for its
  `relay/` code and its own ADRs live there, not here.
