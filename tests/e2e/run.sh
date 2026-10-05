#!/usr/bin/env bash
# End-to-end test: deploys restic-fleet to throwaway systemd containers and checks that
# backups, restores, the dashboard and the security guarantees all work.
#   tests/e2e/run.sh            run and tear down
#   KEEP=1 tests/e2e/run.sh     leave the containers running for inspection
# shellcheck disable=SC2015,SC2016  # "a && ok || die": ok() always succeeds; jq programs are single-quoted on purpose
set -euo pipefail
export MSYS_NO_PATHCONV=1          # Git Bash on Windows: don't rewrite /paths
cd "$(dirname "$0")"

pass=0
ok()   { pass=$((pass + 1)); printf '  \033[32m✔\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
node() { local n=$1; shift; docker exec "rf-e2e-$n" bash -c "$*"; }
ctl()  { docker exec -w /work rf-e2e-controller bash -c "$*"; }
jq_()  { docker exec -i rf-e2e-controller jq "$@"; }   # jq reading stdin

cleanup() {
  if [ "${KEEP:-0}" != 1 ]; then
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf .ssh inventory/credentials
  fi
}
trap cleanup EXIT

step "Start the test fleet"
rm -rf .ssh inventory/credentials
docker compose up -d --build --quiet-pull >/dev/null
for n in server web1 db1; do
  for _ in $(seq 60); do
    state=$(docker exec "rf-e2e-$n" systemctl is-system-running 2>/dev/null || true)
    case $state in running|degraded) break ;; esac
    sleep 1
  done
done
ok "containers up (systemd running)"

mkdir -p .ssh
ctl "ssh-keygen -q -t ed25519 -N '' -f /work/tests/e2e/.ssh/id_ed25519 <<<y >/dev/null 2>&1 || true"
pub=$(cat .ssh/id_ed25519.pub)
for n in server web1 db1; do
  node "$n" "echo '$pub' > /home/tester/.ssh/authorized_keys && chown tester:tester /home/tester/.ssh/authorized_keys && chmod 600 /home/tester/.ssh/authorized_keys"
done
ok "SSH keys installed"

step "Prepare test data"
node web1 "mkdir -p /srv/app/cache && echo 'important' > /srv/app/data.txt && echo 'skip me' > /srv/app/cache/tmp.txt"
node db1 "apt-get update -qq && apt-get install -y -qq postgresql >/dev/null 2>&1 && systemctl start postgresql \
  && runuser -u postgres -- psql -qc \"CREATE DATABASE appdb\" \
  && runuser -u postgres -- psql -q appdb -c \"CREATE TABLE fleet_test (id int, note text); INSERT INTO fleet_test VALUES (1, 'restore-me');\""
ok "web1 files and db1 PostgreSQL database created"

step "Deploy with Ansible"
ctl "ansible --version | head -1"
ctl "ansible-playbook -i tests/e2e/inventory site.yml -e fleet_run_backup_now=true" | tail -n 25
ok "playbook finished"

step "Wait for the first backups"
for n in web1 db1; do
  for _ in $(seq 120); do
    node "$n" "systemctl is-active --quiet restic-fleet-backup.service" || break
    sleep 2
  done
  result=$(node "$n" "systemctl show -p Result --value restic-fleet-backup.service")
  [ "$result" = success ] || { node "$n" "journalctl -u restic-fleet-backup --no-pager -n 40"; die "$n backup result: $result"; }
  ok "$n backup succeeded"
done

step "Restore"
node web1 "rfleet restore latest --tag files --target /tmp/r --include /srv/app >/dev/null && cmp /srv/app/data.txt /tmp/r/srv/app/data.txt" \
  && ok "web1: restored /srv/app/data.txt matches" || die "web1 file restore"
node web1 "[ ! -e /tmp/r/srv/app/cache/tmp.txt ]" && ok "web1: excluded path was not backed up" || die "exclude ignored"
node web1 "rfleet dump --tag generated latest /generated.csv | grep -q '2,world'" && ok "web1: command dump restored" || die "command dump"
node db1 "rfleet dump --tag appdb latest /appdb.sql | grep -q 'restore-me'" \
  && ok "db1: PostgreSQL dump contains the test row" || die "postgres dump restore"
node db1 "rfleet dump --tag appdb latest /appdb.sql > /tmp/appdb.sql && runuser -u postgres -- createdb restored \
  && runuser -u postgres -- psql -q restored < /tmp/appdb.sql >/dev/null \
  && runuser -u postgres -- psql -tAc 'SELECT note FROM fleet_test' restored | grep -qx restore-me" \
  && ok "db1: dump loads into a fresh database" || die "postgres reload"

step "Security guarantees"
before=$(node web1 "rfleet snapshots --json | jq length")
node web1 "rfleet forget \$(rfleet snapshots --json | jq -r '.[0].id') >/dev/null 2>&1 || true"
after=$(node web1 "rfleet snapshots --json | jq length")
[ "$before" = "$after" ] && [ "$before" -ge 1 ] \
  && ok "append-only: a client cannot delete its own backups ($after snapshots kept)" \
  || die "append-only violated: snapshots $before -> $after"
node web1 "set -a; . /etc/restic-fleet/fleet.env; set +a; \
  RESTIC_REPOSITORY=\${RESTIC_REPOSITORY/\/web1\//\/db1\/} restic snapshots >/dev/null 2>&1" \
  && die "private repos violated" || ok "private repos: web1 cannot open db1's repository"
node web1 "stat -c '%a %U' /etc/restic-fleet/fleet.env /etc/restic-fleet/repo.pass" | grep -qv '^600 root' \
  && die "client secrets not 0600 root" || ok "client secrets are 0600 root"
node server "grep -qF ':\$2y\$' /etc/restic-fleet/rest-server.htpasswd" && ok "rest-server passwords are bcrypt" || die "htpasswd"
token=$(cat inventory/credentials/report-token/web1)
node server "grep -qF '$token' /etc/restic-fleet/dashboard.json" && die "plain report token stored on the server" \
  || ok "dashboard stores only token hashes, never the tokens"
code=$(ctl "curl -s -o /dev/null -w '%{http_code}' --cacert tests/e2e/inventory/credentials/ca.crt -X POST \
  -H 'Authorization: Bearer wrong' -d '{}' https://server:8443/api/v1/report")
[ "$code" = 401 ] && ok "dashboard rejects a bad report token" || die "bad token got $code"
code=$(ctl "curl -s -o /dev/null -w '%{http_code}' https://server:8443/ || true")
[ "$code" = 000 ] && ok "dashboard TLS is not trusted without the fleet CA" || die "TLS check got $code"

step "Server maintenance"
node server "systemctl start restic-fleet-maintain.service" \
  && ok "forget/prune/check passed on the server" || { node server "journalctl -u restic-fleet-maintain --no-pager -n 30"; die maintenance; }

step "Dashboard"
pw=$(cat inventory/credentials/dashboard-admin-password)
state=$(ctl "c=\$(mktemp); curl -s --cacert tests/e2e/inventory/credentials/ca.crt -c \$c -o /dev/null \
  -H 'Origin: https://server:8443' --data-urlencode username=admin --data-urlencode password='$pw' https://server:8443/login; \
  curl -s --cacert tests/e2e/inventory/credentials/ca.crt -b \$c https://server:8443/api/v1/state")
echo "$state" | jq_ -r '.hosts[] | "    \(.name): \(.status), maintenance ok=\(.maintenance.ok), snapshots=\(.repo.snapshots)"'
for n in web1 db1; do
  st=$(echo "$state" | jq_ -r --arg n "$n" '.hosts[] | select(.name==$n) | .status')
  [ "$st" = ok ] && ok "dashboard: $n is healthy" || die "dashboard: $n is $st"
done
echo "$state" | jq_ -e '.hosts[] | select(.name=="db1") | .maintenance.ok == true' >/dev/null \
  && ok "dashboard: maintenance result recorded" || die "maintenance not on dashboard"
echo "$state" | jq_ -e '[.hosts[].repo.snapshots] | all(. >= 1)' >/dev/null \
  && ok "dashboard: repository statistics collected" || die "repo stats missing"

step "Idempotency"
out=$(ctl "ansible-playbook -i tests/e2e/inventory site.yml" | tail -n 8)
echo "$out" | grep -E "changed=[1-9]" && die "second run changed something" || ok "second run: changed=0 on every host"

printf '\n\033[32mAll %d checks passed.\033[0m\n' "$pass"
