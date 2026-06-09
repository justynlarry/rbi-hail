# Hail Outreach Automation

Weekly system that queries the RentCast API for active sale listings in zip codes
affected by hail in the last 365 days, generates a PDF report, and emails it to
internal recipients every Monday at 12:00.

---

## Required `.env` Keys

Copy `.env.example` to `.env` and fill in your values (never commit `.env`):

```
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
RENTCAST_API_KEY=
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
```

Optional tuning (can be omitted to use defaults):

```
API_WORKERS=5             # parallel RentCast fetch threads (default: 5)
REPORT_RETENTION_DAYS=90  # days to keep PDF reports on disk (default: 90)
```

---

## Initial Deploy

```bash
# 1. Build and start containers
docker compose up -d --build

# 2. Initialize the database schema
docker compose exec app python scripts/init_db.py

# 3. Import hail events
docker compose exec app python scripts/import_hail_events.py data/rbi_hail_events_2025.csv

# 4. Import the do-not-contact list
docker compose exec app python scripts/import_dnc.py data/rbi_dnc_list.csv

# 5. Add internal email recipients (interactive)
docker compose exec -it app python scripts/add_recipient.py

# 6. Add DNC entries interactively (as needed)
docker compose exec -it app python scripts/add_dnc.py
```

When a new hail events CSV arrives (e.g., `rbi_hail_events_2026.csv`), import it
the same way — the script skips duplicates automatically.

---

## Utility Scripts

All scripts run inside the `app` container via `docker compose exec`.

### Initialize / re-initialize schema
```bash
docker compose exec app python scripts/init_db.py
```
Safe to re-run; uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` throughout.

### Import hail events
```bash
docker compose exec app python scripts/import_hail_events.py data/<filename>.csv
```
CSV must have headers: `Date`, `City`, `Zip`, `Size` (or equivalents like
`hail_date`, `zipcode`, `hail_size`). Skips rows already present by
`hail_date + zipcode` match.

### Import do-not-contact list
```bash
docker compose exec app python scripts/import_dnc.py data/<filename>.csv
```
CSV: one email address per line, no header required. Skips duplicates. Sets
`do_not_contact = TRUE` on any matching agents already in the database.

### Add a single DNC entry interactively
```bash
docker compose exec -it app python scripts/add_dnc.py
```
Prompts for email, validates format, loops until you enter `n`.

### Add an internal report recipient interactively
```bash
docker compose exec -it app python scripts/add_recipient.py
```
Prompts for first name, last name, and email. Loops until you enter `n`.

### Run the weekly job manually
```bash
docker compose exec app python scripts/daily_job.py
```

### Kill a runaway job
The app container is too slim for `pkill`. Use this one-liner to find and kill
the process via bash's built-in:

```bash
docker compose exec app bash -c "kill \$(for p in /proc/[0-9]*; do grep -ql daily_job \$p/cmdline 2>/dev/null && basename \$p; done)"
```

To inspect the PIDs before killing:
```bash
# Find PIDs
docker compose exec app bash -c "for p in /proc/[0-9]*; do grep -ql daily_job \$p/cmdline 2>/dev/null && echo \$(basename \$p); done"

# Then kill by PID
docker compose exec app bash -c "kill <pid1> <pid2>"
```

---

## Systemd Timer Setup

Copy the unit files and enable the timer:

```bash
sudo cp systemd/hail-outreach.service /etc/systemd/system/
sudo cp systemd/hail-outreach.timer   /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now hail-outreach.timer
```

Verify the timer is scheduled:
```bash
systemctl list-timers hail-outreach.timer
```

View logs:
```bash
journalctl -u hail-outreach.service -f
```

The service runs `docker compose exec -T app python scripts/daily_job.py` from
`/home/rbi-user/hail-outreach` every Monday at 12:00 local time. The `-T` flag
disables TTY allocation for non-interactive execution. `Persistent=true` means
if the machine was off at 12:00 Monday, the job fires once on next boot.

The service unit includes an `ExecStartPre` step that verifies the `app`
container is running before attempting to exec into it. If the container is
stopped, the service fails cleanly with a journal entry.

---

## How the Hail Window Works

The job targets zip codes from hail events within a rolling 365-day window
ending today — no config needed at year rollover:

```python
hail_cutoff = date.today() - timedelta(days=365)
# targets: hail_date >= hail_cutoff AND hail_date <= today
```

New hail CSVs can be imported at any time; events enter or leave the window
automatically as dates roll forward.

---

## Script Architecture

```
scripts/
├── db.py              — DB connection factory (3-attempt retry, 5s backoff)
├── init_db.py         — Schema DDL: tables, indexes, idempotent migrations
├── rentcast.py        — RentCast API client (pagination + retry on 429/5xx)
├── pipeline.py        — Per-listing processing: DNC check, upsert, insert
├── report.py          — PDF generation via reportlab
├── mailer.py          — Gmail SMTP sender
├── daily_job.py       — Weekly job orchestrator (main entry point)
├── import_hail_events.py — Bulk CSV importer for hail events
├── import_dnc.py      — Bulk CSV importer for DNC list
├── add_dnc.py         — Interactive single-entry DNC tool
└── add_recipient.py   — Interactive internal email recipient manager
```

---

## Extending the LVM Volume (if disk fills)

Check current usage and volume layout:

```bash
df -h                  # find the full mount point (e.g. /)
sudo vgdisplay         # show volume group name and free extents
sudo lvdisplay         # show logical volume path (e.g. /dev/ubuntu-vg/ubuntu-lv)
```

If the underlying block device has been extended (e.g., cloud disk resize):

```bash
# Resize the physical volume to claim new space
sudo pvresize /dev/sda3          # replace with your PV device

# Extend the logical volume using all free extents
sudo lvextend -l +100%FREE /dev/ubuntu-vg/ubuntu-lv

# Grow the filesystem (ext4)
sudo resize2fs /dev/ubuntu-vg/ubuntu-lv

# For XFS filesystems, use:
# sudo xfs_growfs /
```

Verify:
```bash
df -h
```

The PostgreSQL data volume (`pgdata`) lives inside the LVM-backed filesystem, so
growing the LV/FS is all that's needed — no Docker reconfiguration required.
