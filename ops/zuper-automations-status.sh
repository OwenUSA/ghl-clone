#!/usr/bin/env bash
# Read-only health check of every automation that touches Zuper (docs/ZUPER-AUTOMATIONS.md).
# Run from your own machine: bash ops/zuper-automations-status.sh
# It only reads files, container state and the CRM database. It changes nothing, calls no
# Zuper write, and prints no customer data (no names, phones or addresses — the routes log
# holds addresses, so only its timestamps and counts are shown).
set -u
SSH="ssh -o ConnectTimeout=10 -o BatchMode=yes"

echo "=================== dispatch ==================="
$SSH dispatch 'bash -s' <<'REMOTE'
echo "-- cron entries that touch Zuper"
grep -H -v "^#" /etc/cron.d/* 2>/dev/null | grep -iE "zuper|zbrowser|zsup" || echo "   (none)"
echo "-- safety gate (last run by the routes wrapper)"
sed 's/^/   /' /tmp/zuper-routes.gate 2>/dev/null || echo "   no gate output yet"
echo "-- routes: last run"
ls -l --time-style=+%F_%T /tmp/zuper-routes.out 2>/dev/null | awk '{print "   output written " $6 " (server time)"}'
head -c 300 /tmp/zuper-routes.out | grep -E "^[0-9]{4}-" | head -1 | sed 's/^/   /'
echo "-- routes: problems in the last 500 log lines"
tail -500 /home/qa/zbrowser/routes_sync.log | grep -cE "Traceback|SAFETY GATE FAILED" | sed 's/^/   tracebacks + gate failures: /'
tail -500 /home/qa/zbrowser/routes_sync.log | grep -cE "WARN" | sed 's/^/   WARN lines: /'
REMOTE

echo "=================== owen-main ==================="
$SSH owen-main 'bash -s' <<'REMOTE'
cd /opt/santiagoproperties || exit 1
echo "-- containers"
docker ps -a --format '   {{.Names}}\t{{.Status}}' | grep -E "zuper_|ghl_clone_(api|worker)"
echo "-- zuper_tasks: last applied change"
grep -E "^20[0-9-]+T|^APPLIED" zuper-tasks/data/tasks_sync.log | grep -B1 "^APPLIED" | tail -2 | sed 's/^/   /'
echo "   log last written: $(date -r zuper-tasks/data/tasks_sync.log '+%F %T %Z')  (only changes and errors are logged)"
echo "   errors in last 200 lines: $(tail -200 zuper-tasks/data/tasks_sync.log | grep -cE 'Traceback|SAFETY GATE FAILED| -> 5[0-9][0-9] ')"
echo "-- zuper_proposals: last rebuild"
grep -E "^20[0-9-]+T|rebuild" zuper-proposals/data/compare_sync.log | grep -B1 "rebuild" | tail -2 | sed 's/^/   /'
echo "   errors in last 200 lines: $(tail -200 zuper-proposals/data/compare_sync.log | grep -cE 'Traceback|SAFETY GATE FAILED')"
echo "-- CRM switches (.env.prod, secrets not shown)"
grep -E "^(ZUPER_(SYNC_ENABLED|PULL_ONLY|AHS_EMAIL_CREATES_JOBS|PROPOSAL_LINES)|DISPATCH_(ENABLED|ZUPER_WRITES))=" ghl-clone/.env.prod | sed 's/^/   /'
grep -E "^CRM_LINK_(EMAIL_JOBS_ENABLED|AHS_AUTHORIZATIONS_ENABLED)=" owen-main/.env.prod | sed 's/^/   owen-main: /'
echo "-- CRM heartbeats"
cd ghl-clone && docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T api python - <<'PY'
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal()
q = lambda s: db.execute(text(s)).fetchone()
s = q("select last_sweep_success_at, last_error_at, last_webhook_at, last_history_day from zuper_sync_state")
print("   sweep last success:", s[0], "| last error at:", s[1])
print("   webhook last received:", s[2], "(none ever = sweep only)")
print("   history pass last day:", s[3])
r = db.execute(text("select * from dispatch_state")).mappings().fetchone() or {}
print("   dispatch pass:", {k: v for k, v in r.items() if k.endswith("_at") or k == "enabled"})
print("   proposal lines rows:", q("select count(*) from zuper_proposal_lines")[0])
print("   AHS email sends, last 7 days:",
      db.execute(text("select status, count(*) from jobs where type='zuper_send' "
                      "and created_at > now() - interval '7 days' group by 1")).fetchall())
print("   digest notes posted / tried:", q("select count(note_uid), count(*) from zuper_digests"))
PY
echo "-- nightly KPI workbooks (newest)"
ls -t /opt/santiagoproperties/ghl-clone-kpi/reports | head -2 | sed 's/^/   /'
REMOTE
