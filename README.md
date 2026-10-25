# AP3X tracker API (rh_tracker)

Source of the rackmain1 tracker backend behind the wall's `/api/*` routes, imported from the live host on 2026-10-25. The live copy still runs from `~/scripts/rh_tracker/`; this repository is its reviewed history, and `tracker-release` installs from it.

## Layout

| Path | Live location | What it is |
| --- | --- | --- |
| `rh_tracker/tracker_api.py` | `~/scripts/rh_tracker/` | FastAPI app (uvicorn on `127.0.0.1:8000`, unit `rh-tracker-api.service`): status, ops state/brief/feed, workloads (MA001), weather, finance, market sentinel, camera, Plex update state, and more |
| `rh_tracker/ops_state.py` | same | Ops state, events, trends, threat and latency tracking used by the API |
| `rh_tracker/media_activity.py` | same | MA001 media workloads (qBittorrent, Sonarr, Radarr, Plex counts) |
| `rh_tracker/trackerctl.py`, `voo_nvda_tracker.py` | same | Share/portfolio helpers imported by the API; the VOO/NVDA tracker (`run_tracker.sh`) |
| `rh_tracker/priority_alpha_*.py`, `*.sh` | same | Priority Alpha agent, daily report, and backup, run by their own units |
| `rh_tracker/run_*.sh` | same | Launchers (`run_tracker_api.sh` starts uvicorn from `/home/ap3x/rh_env`) |
| `rh_tracker/rh-tracker-api.service.txt` | `/etc/systemd/system/rh-tracker-api.service` | The unit, without `Environment=` lines |
| `ma001/` | `~/AP3X_MEDIA_ACTIVITY_MA001R2_20260806/` | The MA001 release package of 2026-08-06 (payload, tests, certify/deploy/rollback). Its `payload/tracker_api.py` predates later API changes; the live file is `rh_tracker/tracker_api.py`. |

## Deliberately not in this repository

Credentials and personal data stay on rackmain1 and are ignored by `.gitignore`: `config/*.env` (Discord webhook, agent actions), `robinhood_snapshot.json`, `portfolio_log.csv`, `.tracker_shares`, every `*.json` state file, and `state/` (dated `.bak` copies, runtime state, network scans). The import ran a secret scan; its only match was a fake test value in `ma001/tests/test_media_activity.py`.

## Tests

CI (and `tracker-release`) compiles every Python file, checks shell syntax, runs `python -m unittest discover -s ma001/tests`, and runs `node ma001/tests/test_frontend_contract.js`. None of them need the API's packages.

## Releasing on rackmain1

`deploy/rackmain1-release.sh` (alias `tracker-release`), like `wall-release`:

1. fetches `main` and continues from the fetched copy of itself;
2. runs the checks above;
3. plans each file in `rh_tracker/` as `CURRENT`, `REPLACE`, or `NEW`, refusing any live file that matches no version in this repository's history (an edit made on the host must be committed here first);
4. asks, backs up every replaced file to `~/tracker-releases/backups/`, and installs;
5. if an API module changed (`tracker_api.py`, `ops_state.py`, `media_activity.py`, `trackerctl.py`, `voo_nvda_tracker.py`), restarts `rh-tracker-api` with sudo and waits up to a minute for `/health`, `/status`, and `/api/ops/workloads` to answer; if they do not, it restores the backups, restarts again, and stops with the journal command to check;
6. prints an undo command, also saved as `UNDO.sh` beside the backups.

`--plan` shows the plan only; `--yes` skips the prompt. Other scripts take effect at their next scheduled run.

One-time setup on rackmain1: the clone at `~/src/ap3x-tracker-api` pushes and fetches with the deploy key `~/.ssh/ap3x_tracker_api`, and

```bash
echo "alias tracker-release='~/src/ap3x-tracker-api/deploy/rackmain1-release.sh'" >> ~/.bashrc && source ~/.bashrc
```
