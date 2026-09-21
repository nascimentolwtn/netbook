# digital_frame.sh

Lightweight photo slideshow display service for netbook. Continuously cycles through curated photos on a desktop monitor using `feh` image viewer, optimized for low-resource i386 hardware.

## Purpose

- Auto-rotating slideshow of photos from configurable folder
- Minimal RAM/CPU footprint (~50MB)
- Always-on display with DPMS disabled (monitor won't sleep)
- Keyboard-driven navigation and deletion

## Usage

```bash
./digital_frame.sh
```

Starts the slideshow manually. **Note:** This script is configured to auto-run on the user's desktop when the netbook boots, so it will start automatically on power-up. Control it with:
- **h/j/k/l** — navigate (left/down/up/right)
- **space** — pause/resume
- **d** — delete current photo (moved to soft-delete folder)
- **q** — quit

## Configuration

Edit these variables at the top of the script:

- `photo_root` — folder containing photos to display
- `delay` — seconds between photo changes (default: 15)
- `screen_res` — monitor resolution (default: 1024x600)
- `queue_cap` / `queue_slots` — lookahead queue size (performance tuning)

## Performance

- Uses **weighted folder selection** to prevent small folders from dominating rotation
- **Caches file listings** and folder weights to speed up large archives (~360GB)
- **Pre-scales images** to screen resolution in background queue (24-36 ahead)

## Diagnostics

Crash logs and breadcrumbs stored in:
- `~/.cache/digitalframe_crashes.log` — crash history (500-entry rotating buffer)
- `~/.cache/digitalframe_breadcrumbs.log` — recent operations (50-entry buffer)

## See Also

- Parent README: `/README.md` — three-app ecosystem overview
- Git history: `git log` — version evolution and design decisions
