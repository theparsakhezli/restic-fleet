#!/usr/bin/env bash
# restic-fleet setup: an interactive wizard around the Ansible playbooks.
#
#   ./setup.sh                      menu
#   ./setup.sh new                  create a fleet (asks questions, writes an inventory, deploys)
#   ./setup.sh add-client [fleet]   add a machine to an existing fleet
#   ./setup.sh deploy [fleet] [ansible-playbook args...]
#   ./setup.sh info [fleet]         dashboard address and login
#   ./setup.sh check [fleet]        test SSH access to every machine
#
# Fleets live in inventories/<name>/. Everything the wizard writes is plain YAML you can edit.
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"

ANSIBLE_SPEC='ansible-core>=2.16,<2.20'
VENV=.venv
NAME_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$'

# ------------------------------------------------------------------ output helpers
if [ -t 1 ]; then
  B=$'\033[1m' D=$'\033[2m' G=$'\033[32m' Y=$'\033[33m' R=$'\033[31m' C=$'\033[36m' N=$'\033[0m'
else
  B='' D='' G='' Y='' R='' C='' N=''
fi
say()   { printf '%s\n' "$*"; }
title() { printf '\n%s%s%s\n' "$B" "$*" "$N"; }
good()  { printf '%s✔%s %s\n' "$G" "$N" "$*"; }
warn()  { printf '%s!%s %s\n' "$Y" "$N" "$*" >&2; }
fail()  { printf '%s✘ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

# ask VAR "Question" [default] [validator-regex] [error-message]
ask() {
  local __var=$1 prompt=$2 default=${3:-} re=${4:-} msg=${5:-"That doesn't look right."} answer
  while true; do
    if [ -n "$default" ]; then
      read -r -p "${C}?${N} ${prompt} ${D}[${default}]${N} " answer || exit 1
      answer=${answer:-$default}
    else
      read -r -p "${C}?${N} ${prompt} " answer || exit 1
    fi
    answer=$(printf '%s' "$answer" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    # no validator = anything goes (including empty); otherwise the answer must match it
    # (validators for optional questions also match the empty string)
    if [ -z "$re" ] || [[ $answer =~ $re ]]; then
      printf -v "$__var" '%s' "$answer"
      return
    fi
    warn "$msg"
  done
}

yes_no() {  # yes_no "Question" y|n  -> exit status
  local default=${2:-n} answer hint="[y/N]"
  [ "$default" = y ] && hint="[Y/n]"
  while true; do
    read -r -p "${C}?${N} $1 ${D}${hint}${N} " answer || exit 1
    answer=${answer:-$default}
    case ${answer,,} in y|yes) return 0 ;; n|no) return 1 ;; esac
  done
}

choose() {  # choose VAR "Question" option1 option2 ...   (first = default)
  local __var=$1 prompt=$2 i answer
  shift 2
  say "${C}?${N} $prompt"
  i=1
  for opt in "$@"; do printf '    %s%d)%s %s\n' "$B" "$i" "$N" "$opt"; i=$((i + 1)); done
  while true; do
    read -r -p "  choice ${D}[1]${N} " answer || exit 1
    answer=${answer:-1}
    if [[ $answer =~ ^[0-9]+$ ]] && [ "$answer" -ge 1 ] && [ "$answer" -le $# ]; then
      printf -v "$__var" '%s' "${!answer}"
      return
    fi
  done
}

q() { local s=${1//\'/\'\'}; printf "'%s'" "$s"; }       # YAML single-quoted string

# ------------------------------------------------------------------ prerequisites
ANSIBLE_PLAYBOOK=ansible-playbook
ANSIBLE=ansible

ensure_ansible() {
  if [ -x "$VENV/bin/ansible-playbook" ]; then
    ANSIBLE_PLAYBOOK=$VENV/bin/ansible-playbook ANSIBLE=$VENV/bin/ansible
    return
  fi
  if command -v ansible-playbook >/dev/null 2>&1 \
     && ansible-playbook --version 2>/dev/null | head -1 | grep -Eq 'core 2\.(1[6-9])'; then
    return
  fi
  command -v python3 >/dev/null 2>&1 || fail "python3 is required (apt install python3 python3-venv)."
  title "Installing Ansible into $VENV (one time)"
  python3 -m venv "$VENV" || fail "Could not create a virtualenv. On Debian/Ubuntu: apt install python3-venv"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet "$ANSIBLE_SPEC"
  ANSIBLE_PLAYBOOK=$VENV/bin/ansible-playbook ANSIBLE=$VENV/bin/ansible
  good "$("$ANSIBLE_PLAYBOOK" --version | head -1)"
}

pick_fleet() {  # pick_fleet VAR [given-name]
  local __var=$1 given=${2:-} fleets=()
  if [ -n "$given" ]; then
    [ -f "inventories/$given/hosts.yml" ] || fail "No fleet named '$given' (inventories/$given/hosts.yml)."
    printf -v "$__var" '%s' "$given"
    return
  fi
  for d in inventories/*/hosts.yml; do
    [ -e "$d" ] || continue
    d=${d#inventories/}; d=${d%/hosts.yml}
    [ "$d" = example ] || fleets+=("$d")
  done
  case ${#fleets[@]} in
    0) fail "No fleet yet. Run: ./setup.sh new" ;;
    1) printf -v "$__var" '%s' "${fleets[0]}" ;;
    *) choose "$__var" "Which fleet?" "${fleets[@]}" ;;
  esac
}

vault_args() {  # add --ask-vault-pass when the fleet has an encrypted vars file
  # shellcheck disable=SC2016  # matching the literal header of an encrypted file
  if grep -qs '^\$ANSIBLE_VAULT' "inventories/$1/group_vars/all/vault.yml"; then
    printf '%s\n' --ask-vault-pass
  fi
}

# ------------------------------------------------------------------ questions
SCHEDULE='' INTERVAL=''
ask_schedule() {  # sets SCHEDULE (systemd OnCalendar) and INTERVAL (hours)
  local kind at hours days
  choose kind "How often should it back up?" "every day" "every N hours" "every N days"
  case $kind in
    "every day")
      ask at "At what time (server local time, HH:MM)?" "02:30" '^([01][0-9]|2[0-3]):[0-5][0-9]$' "Use HH:MM, e.g. 02:30."
      SCHEDULE="*-*-* ${at}:00" INTERVAL=24 ;;
    "every N hours")
      ask hours "Every how many hours (1-12)?" "6" '^([1-9]|1[0-2])$' "A number from 1 to 12."
      SCHEDULE="*-*-* 00/${hours}:00:00" INTERVAL=$hours ;;
    "every N days")
      ask days "Every how many days (2-14)?" "3" '^([2-9]|1[0-4])$' "A number from 2 to 14."
      ask at "At what time (HH:MM)?" "02:30" '^([01][0-9]|2[0-3]):[0-5][0-9]$' "Use HH:MM, e.g. 02:30."
      SCHEDULE="*-*-01/${days} ${at}:00" INTERVAL=$((days * 24)) ;;
  esac
}

# Appends one client's YAML to the file given as $1 ($2: existing inventory to check names against).
ask_client() {
  local out=$1 name address paths excludes dbtype where container database user dbname cmd filename first
  title "Machine to back up"
  while true; do
    ask name "Name (becomes its repository name)" "" "$NAME_RE" "Letters, digits, . _ - only."
    # names must be unique across the new entries and the existing inventory ($2, if any)
    if grep -Eqs "^        ${name}:" "$out" ${2:+"$2"}; then
      warn "A machine named $name already exists in this fleet."
    else
      break
    fi
  done
  ask address "IP address or DNS name of $name" "" '^[A-Za-z0-9.:-]+$' "An IP address or host name."
  ask paths "Folders to back up (comma-separated, empty for none)" "/etc" '' ''
  ask excludes "Paths or patterns to skip (comma-separated, optional)" "" '' ''
  ask_schedule
  {
    printf '        %s:\n' "$name"
    printf '          ansible_host: %s\n' "$(q "$address")"
    printf '          backup_schedule: %s\n' "$(q "$SCHEDULE")"
    printf '          backup_interval_hours: %s\n' "$INTERVAL"
    printf '          backup_paths:'
    if [ -n "$paths" ]; then printf '\n'; IFS=',' read -ra arr <<<"$paths"
      for p in "${arr[@]}"; do p=$(echo "$p" | xargs); [ -n "$p" ] && printf '            - %s\n' "$(q "$p")"; done
    else printf ' []\n'; fi
    printf '          backup_excludes:'
    if [ -n "$excludes" ]; then printf '\n'; IFS=',' read -ra arr <<<"$excludes"
      for p in "${arr[@]}"; do p=$(echo "$p" | xargs); [ -n "$p" ] && printf '            - %s\n' "$(q "$p")"; done
    else printf ' []\n'; fi
  } >>"$out"

  first=1
  while yes_no "Back up a database on $name?" n; do
    [ $first = 1 ] && printf '          backup_databases:\n' >>"$out"
    first=0
    choose dbtype "Database type" "postgres" "mysql" "mongodb" "command"
    ask dbname "A short name for this backup job" "$dbtype" "$NAME_RE" "Letters, digits, . _ - only."
    {
      printf '            - name: %s\n' "$(q "$dbname")"
      printf '              type: %s\n' "$dbtype"
    } >>"$out"
    if [ "$dbtype" = command ]; then
      ask cmd "Command that writes the backup to stdout" "" '.+' "A command is required."
      ask filename "File name to store it as" "$dbname.dump" '^[A-Za-z0-9._-]+$' "Letters, digits, . _ - only."
      printf '              command: %s\n              filename: %s\n' "$(q "$cmd")" "$(q "$filename")" >>"$out"
      continue
    fi
    choose where "Where does it run?" "in a Docker container" "directly on the machine"
    if [ "$where" = "in a Docker container" ]; then
      ask container "Container name" "" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "A container name (docker ps)."
      printf '              container: %s\n' "$(q "$container")" >>"$out"
    fi
    ask database "Database to dump ('all' for every database)" "all" '^[A-Za-z0-9_$.-]+$' "Letters, digits, _ $ . - only."
    printf '              database: %s\n' "$(q "$database")" >>"$out"
    case $dbtype in
      postgres) ask user "Database user" "postgres" '^[A-Za-z0-9_.-]+$' "Letters, digits, _ . - only." ;;
      mysql)    ask user "Database user (password comes from the container's MYSQL_ROOT_PASSWORD, or --defaults-extra-file natively)" "root" '^[A-Za-z0-9_.-]+$' "Letters, digits, _ . - only." ;;
      *)        user="" ;;
    esac
    [ -n "$user" ] && printf '              user: %s\n' "$(q "$user")" >>"$out"
  done
  [ $first = 1 ] && printf '          backup_databases: []\n' >>"$out"
  good "$name added"
}

# ------------------------------------------------------------------ commands
cmd_new() {
  local fleet ssh_user key server server_addr data_dir tg_token tg_chats webhook tmp
  title "restic-fleet: new backup fleet"
  say "You'll need: SSH access (with sudo) to one backup server and to every machine to back up."
  say "Nothing is changed on any machine until you confirm at the end."
  ask fleet "Fleet name (a folder under inventories/)" "prod" "$NAME_RE" "Letters, digits, . _ - only."
  [ -e "inventories/$fleet" ] && fail "inventories/$fleet already exists. Use ./setup.sh add-client $fleet"
  ask ssh_user "SSH user on all machines (needs sudo)" "${SUDO_USER:-root}" '^[a-z_][a-z0-9_.-]*$' "A Unix user name."
  ask key "SSH private key (empty = your SSH agent / default key)" "" '' ''
  if [ -n "$key" ] && [ ! -f "${key/#\~/$HOME}" ]; then warn "$key does not exist here; keeping it anyway."; fi

  title "Backup server"
  say "${D}Stores every repository, runs the dashboard and weekly maintenance. Give it plenty of disk.${N}"
  ask server "Name" "backup" "$NAME_RE" "Letters, digits, . _ - only."
  ask server_addr "IP address or DNS name clients will use" "" '^[A-Za-z0-9.:-]+$' "An IP address or host name."
  ask data_dir "Where to store the repositories" "/srv/restic-fleet" '^/[^[:space:]]*$' "An absolute path."

  tmp=$(mktemp)
  trap 'rm -f "$tmp"' RETURN
  ask_client "$tmp"
  while yes_no "Add another machine?" n; do ask_client "$tmp"; done

  title "Notifications (optional)"
  say "${D}The dashboard alerts on failed or overdue backups and when they recover.${N}"
  ask tg_token "Telegram bot token (empty to skip)" "" '' ''
  tg_chats=""
  [ -n "$tg_token" ] && ask tg_chats "Telegram chat IDs (comma-separated)" "" '^-?[0-9]+(,[ ]*-?[0-9]+)*$' "Numeric chat IDs."
  ask webhook "Webhook URL (Slack/Discord/…; empty to skip)" "" '^(https://.+)?$' "Must start with https://"

  mkdir -p "inventories/$fleet/group_vars/all"
  {
    printf '# restic-fleet inventory for "%s" (written by setup.sh; safe to edit).\n' "$fleet"
    printf 'all:\n  vars:\n    ansible_user: %s\n' "$(q "$ssh_user")"
    [ -n "$key" ] && printf '    ansible_ssh_private_key_file: %s\n' "$(q "$key")"
    printf '  children:\n    backup_server:\n      hosts:\n        %s:\n          ansible_host: %s\n' "$server" "$(q "$server_addr")"
    printf '    backup_clients:\n      hosts:\n'
    cat "$tmp"
  } >"inventories/$fleet/hosts.yml"
  {
    printf '# Fleet-wide settings; every option is documented in roles/fleet_common/defaults/main.yml\n'
    printf 'fleet_name: %s\n' "$(q "$fleet")"
    printf 'fleet_data_dir: %s\n' "$(q "$data_dir")"
    [ -n "$webhook" ] && printf 'fleet_notify_webhook_url: %s\n' "$(q "$webhook")"
    if [ -n "$tg_chats" ]; then
      printf 'fleet_notify_telegram_chat_ids:\n'
      IFS=',' read -ra arr <<<"$tg_chats"
      for c in "${arr[@]}"; do printf '  - %s\n' "$(q "$(echo "$c" | xargs)")"; done
    fi
  } >"inventories/$fleet/group_vars/all/main.yml"
  if [ -n "$tg_token" ]; then
    printf 'fleet_notify_telegram_token: %s\n' "$(q "$tg_token")" >"inventories/$fleet/group_vars/all/vault.yml"
    chmod 600 "inventories/$fleet/group_vars/all/vault.yml"
    ensure_ansible
    if yes_no "Encrypt the Telegram token with ansible-vault? (you'll type a vault password on every deploy)" y; then
      "${ANSIBLE_PLAYBOOK%-playbook}-vault" encrypt "inventories/$fleet/group_vars/all/vault.yml"
    fi
  fi
  good "Wrote inventories/$fleet/"

  title "Review"
  sed 's/^/  /' "inventories/$fleet/hosts.yml"
  if yes_no "Deploy now?" y; then
    cmd_check "$fleet"
    cmd_deploy "$fleet" -e fleet_run_backup_now=true
  else
    say "Deploy later with: ${B}./setup.sh deploy $fleet${N}"
  fi
}

cmd_add_client() {
  local fleet tmp
  pick_fleet fleet "${1:-}"
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' RETURN
  ask_client "$tmp" "inventories/$fleet/hosts.yml"
  # append under backup_clients.hosts (the wizard-generated layout keeps it last)
  cp "inventories/$fleet/hosts.yml" "inventories/$fleet/hosts.yml.bak"
  cat "$tmp" >>"inventories/$fleet/hosts.yml"
  ensure_ansible
  if ! "${ANSIBLE}-inventory" -i "inventories/$fleet" --list >/dev/null 2>&1; then
    mv "inventories/$fleet/hosts.yml.bak" "inventories/$fleet/hosts.yml"
    fail "The inventory would become invalid (was it edited by hand?). Add the host manually."
  fi
  rm -f "inventories/$fleet/hosts.yml.bak"
  good "Added to inventories/$fleet/hosts.yml"
  if yes_no "Deploy now? (updates the server, then installs the new machine)" y; then
    cmd_deploy "$fleet" -e fleet_run_backup_now=true
  fi
}

cmd_check() {
  local fleet
  pick_fleet fleet "${1:-}"
  ensure_ansible
  title "Checking SSH + sudo on every machine"
  # shellcheck disable=SC2046
  "$ANSIBLE" -i "inventories/$fleet" all -b -m ansible.builtin.ping $(vault_args "$fleet") \
    || fail "Fix SSH access above (ssh-copy-id, sudo rights), then run: ./setup.sh deploy $fleet"
  good "All machines reachable"
}

cmd_deploy() {
  local fleet
  pick_fleet fleet "${1:-}"
  shift || true
  ensure_ansible
  title "Deploying $fleet"
  # shellcheck disable=SC2046
  "$ANSIBLE_PLAYBOOK" -i "inventories/$fleet" site.yml $(vault_args "$fleet") "$@"
  cmd_info "$fleet"
}

cmd_info() {
  local fleet creds server addr
  pick_fleet fleet "${1:-}"
  creds="inventories/$fleet/credentials"
  server=$(awk '/backup_server:/{f=1} f && /^        [A-Za-z0-9]/{sub(":","",$1); print $1; exit}' "inventories/$fleet/hosts.yml")
  addr=$(awk -v s="        $server:" '$0==s{f=1;next} f && /ansible_host:/{gsub(/[\x27"]/,"",$2); print $2; exit}' "inventories/$fleet/hosts.yml")
  title "Dashboard"
  say "  URL:      https://${addr}:8443"
  say "  User:     admin"
  if [ -f "$creds/dashboard-admin-password" ]; then
    say "  Password: $(cat "$creds/dashboard-admin-password")"
  else
    say "  Password: (created on first deploy)"
  fi
  say "  ${D}The certificate comes from your fleet's own CA: import $creds/ca.crt to silence the browser warning.${N}"
  warn "Back up $creds/ somewhere safe: without the repository passwords in it, backups can't be restored."
}

menu() {
  local action
  title "restic-fleet"
  choose action "What do you want to do?" "Create a new backup fleet" "Add a machine to a fleet" \
    "Deploy / update a fleet" "Show the dashboard login" "Check SSH access"
  case $action in
    "Create a new backup fleet") cmd_new ;;
    "Add a machine to a fleet") cmd_add_client ;;
    "Deploy / update a fleet") cmd_deploy ;;
    "Show the dashboard login") cmd_info ;;
    "Check SSH access") cmd_check ;;
  esac
}

case ${1:-} in
  "") menu ;;
  new) cmd_new ;;
  add-client) shift; cmd_add_client "$@" ;;
  deploy) shift; cmd_deploy "$@" ;;
  info) shift; cmd_info "$@" ;;
  check) shift; cmd_check "$@" ;;
  -h|--help|help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) fail "Unknown command '$1'. See ./setup.sh --help" ;;
esac
