# AP3X Media Activity MA001R2

MA001 replaces the process/CPU-based `WORKLOADS` detector with a read-only,
state-based media contract.

MA001R2 retains the MA001R1 backup-leaf safety correction and repairs a
post-install certifier contradiction. The certifier now permits only the
required public `display.title` field while continuing to reject media-title
fields everywhere else in the response. The media feature payload remains
byte-for-byte identical to MA001 and MA001R1.

The deployer uses `sudo` solely to create the new timestamped backup leaf
beneath the protected `/home/ap3x/system_fix_backups` root, assigns that leaf
to `ap3x`, and leaves the parent directory's ownership and permissions
unchanged.

## Wall behavior

- `MEDIA ACTIVE` — one or more Plex transcodes or qBittorrent torrents in
  `downloading`, `forcedDL`, or `metaDL`.
- `MEDIA ATTENTION` — a `stalledDL` torrent is still tracked by Sonarr/Radarr,
  or a completed ARR queue item is waiting for import.
- `MEDIA IDLE` — all four sources are healthy and no current activity or
  attention condition exists.
- `MEDIA PARTIAL` — at least one source cannot be read, so MA001 refuses to
  claim that media is idle.

Paused, queued, completed, seeding, missing-file, and abandoned qBittorrent
entries are not counted as active downloads. An untracked `stalledDL` entry is
also ignored, preventing old qBittorrent history from producing a permanent
wall banner.

## Data and security boundary

- Plex uses the existing token-safe `/usr/local/sbin/plex-session-count-json`
  helper. If it is unavailable, MA001 falls back to exact `Plex Transcoder`
  process counting and marks Plex `PARTIAL`.
- qBittorrent is read through its local WebUI API on port `8090`.
- Sonarr and Radarr API keys are read inside the process from their containers,
  used only as request headers, and never returned or logged.
- The endpoint returns grouped counts only—no torrent hashes, media titles,
  process arguments, API keys, or Plex tokens.
- MA001 performs no pause, resume, delete, import, queue, download, Plex,
  Docker-control, or scoring action.

## Live write boundary

1. `/home/ap3x/scripts/rh_tracker/media_activity.py` (new)
2. `/home/ap3x/scripts/rh_tracker/tracker_api.py` (one endpoint section)
3. `/home/ap3x/stacks/media-automation/wall-ui/local-ops-canary.js`
   (`renderWorkloads` only)
4. `/home/ap3x/stacks/media-automation/wall-ui/local-ops-canary.html`
   (cache token `ah001` to `ma001` only)

The tracker API is restarted once. The kiosk is not restarted; WR003 loads the
new Local Ops JavaScript on the next rotation.

## Deploy

From ApexTower PowerShell:

```powershell
scp "D:\All Downloads\AP3X_MEDIA_ACTIVITY_MA001R2_20260806.tar.gz" `
  ap3x@192.168.4.115:/home/ap3x/
```

On `rackmain1`:

```bash
cd /home/ap3x
tar -xzf AP3X_MEDIA_ACTIVITY_MA001R2_20260806.tar.gz
cd AP3X_MEDIA_ACTIVITY_MA001R2_20260806
chmod +x deploy_MA001.sh rollback_MA001.sh certify_MA001.sh
./deploy_MA001.sh
```

The deployer prints the exact timestamped rollback command after successful
certification.

## Rollback ordering

MA001 depends on AH001 and AS001R1. Roll back MA001 before either of those
features. The guards refuse partial, drifted, or reversed states.
