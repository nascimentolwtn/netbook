# Syncthing Configuration

Backup hub for family photo galleries. Syncthing daemon runs on the netbook as a systemd service, syncing photos from family smartphones to `/media/backup/sync_data/`.

## Synchronized Folders

All folders are bi-directional (`sendreceive`):

| Folder | Path | Source Devices |
|--------|------|---|
| **Photos_Aline** | `/media/backup/sync_data/Photos_Aline` | Aline's smartphone |
| **Photos_LW** | `/media/backup/sync_data/Photos_LW` | LW's phone (paused) |
| **Photos_Lais** | `/media/backup/sync_data/Photos_Lais` | Lais's smartphone |
| **Photos_Paty** | `/media/backup/sync_data/Photos_Paty` | Paty's smartphone |

## Connected Devices

- **aspire-one** (netbook) — this device, acts as the sync hub
- **Aline SM-S926B** — syncing actively
- **LW_Phone** — syncing actively  
- **LW Samsung 22** — paused (not currently syncing)

## Configuration

**Web UI:** `http://localhost:8384` (on netbook)

**Sync ports:**
- Service: `tcp://0.0.0.0:22000` (file sync)
- Discovery: `udp://[ff12::8384]:21027` (LAN device discovery)

**Key settings:**
- Rescan interval: 3600s (1 hour)
- Filesystem watcher: enabled (detects new photos immediately)
- Minimum disk free: 1%
- Conflicting versions kept: 10

## Files Included

- `config.xml` — current configuration (device IDs, folder setup, sync settings)
- `config.xml.bak` — backup configuration
- `cert.pem` / `key.pem` — TLS device certificates (for sync protocol)
- `https-cert.pem` / `https-key.pem` — HTTPS certificates (web UI)

## Secondary Backup

The netbook's `/home` is synced to a Windows PC at `C:\Users\lw_na\Syncthing\` for disaster recovery.

## See Also

- Parent README: `/README.md` — three-app ecosystem overview
- Syncthing docs: https://docs.syncthing.net/
