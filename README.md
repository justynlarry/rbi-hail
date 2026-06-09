# Hail Outreach Automation

Daily system that queries the RentCast API for active sale listings in zip codes
affected by hail the previous year, generates a PDF report, and emails it to
internal recipients.

---

## Required `.env` Keys

Create a `.env` file in the project root with these keys (values not shown):

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

The `.env` file is never committed to git.

---

## Initial Deploy

```bash
# 1. Build and start containers
docker compose up -d --build

# 2. Initialize the database schema
docker compose exec app python scripts/init_db.py

# 3. Import last year's hail events
docker compose exec app python scripts/import_hail_events.py data/rbi_hail_events_2025.csv

# 4. Import the do-not-contact list
docker compose exec app python scripts/import_dnc.py data/rbi_dnc_list.csv

# 5. Add internal email recipients (repeat as needed)
docker compose exec -it app python scripts/add_dnc.py   # for DNC additions
# To add internal email recipients, connect to the DB directly:
docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "INSERT INTO internal_email (emp_fname, emp_lname, emp_email) VALUES ('First', 'Last', 'email@example.com');"
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
Safe to re-run; uses `CREATE TABLE IF NOT EXISTS` throughout.

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

### Run the daily job manually
```bash
docker compose exec app python scripts/daily_job.py
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
`/home/rbi-user/hail-outreach` each morning at 06:00 local time. The `-T` flag
disables TTY allocation for non-interactive execution. `Persistent=true` means
if the machine was off at 06:00, the job fires once on next boot.

---

## How REPORT_YEAR Works

`REPORT_YEAR` is calculated at runtime in every script that needs it:

```python
from datetime import date
REPORT_YEAR = date.today().year - 1
```

In 2026, `REPORT_YEAR = 2025`. In 2027, `REPORT_YEAR = 2026` — no config
change needed at year rollover. The daily job targets zip codes from hail events
in that year automatically.

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
