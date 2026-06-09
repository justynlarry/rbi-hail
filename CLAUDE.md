# Hail Outreach Automation

## Project Context
Multi-container hail outreach automation system for the Greater Denver Area.
Queries RentCast API daily, matches listings to hail-affected zip codes,
generates a PDF report, and emails it to internal recipients.

## Stack
- Python 3.12
- PostgreSQL 16
- Docker Compose (two containers: db, app)
- reportlab for PDF generation
- smtplib for email via Gmail SMTP

## Project Structure
- scripts/     — all Python scripts
- data/        — CSV imports (hail events, do not contact list)
- reports/     — generated PDF reports
- logs/        — log output
- .env         — secrets (never commit)

## Code Standards
- All secrets from environment variables, never hardcoded
- All DB connections use psycopg2 with 3-attempt retry, 5s backoff
- All scripts log to stdout with timestamps via Python logging at INFO level
- Agent name and brokerage: write on INSERT only, never UPDATE
- ON CONFLICT DO NOTHING on all affected_listings inserts

## Key Rules
- REPORT_YEAR = date.today().year - 1 (calculated at runtime, never from .env)
- RentCast API key passed via X-Api-Key header
- Paginate all RentCast queries via offset until results < page size
- Filter listings to status=Active and status=Pending only
- Skip any listing whose agent email is in do_not_contact_list

