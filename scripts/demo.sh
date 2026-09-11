#!/usr/bin/env bash
#
# CloudGuardian AI — guided live demo (Phase 10)
# ---------------------------------------------------------------
# Walks the full detect -> decide -> act -> verify loop live, plus the
# predictive and observability panels, from one command.
#
# Prereqs (see README Parts 3-5): k3d cluster up, fleet deployed,
# docker-compose platform stack running.
#
# Usage:
#   bash scripts/demo.sh            interactive menu
#   bash scripts/demo.sh --status   non-interactive: fleet + platform + observability summary
#   bash scripts/demo.sh --all      non-interactive: status, then run the full chaos -> heal demo
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ---------- colors ----------
if [ -t 1 ]; then
  C_BOLD=$'\033[1m'; C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YEL=$'\033[33m'
  C_RED=$'\033[31m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_BOLD=""; C_CYAN=""; C_GREEN=""; C_YEL=""; C_RED=""; C_DIM=""; C_RESET=""
fi

say()  { printf '%s\n' "[$(date +%H:%M:%S)] $*"; }
ok()   { printf '%s%s✓ %s%s\n' "$C_GREEN" "$C_BOLD" "$*" "$C_RESET"; }
info() { printf '%s%s→ %s%s\n' "$C_CYAN" "$C_BOLD" "$*" "$C_RESET"; }
warn() { printf '%s%s! %s%s\n' "$C_YEL" "$C_BOLD" "$*" "$C_RESET"; }
err()  { printf '%s%s✗ %s%s\n' "$C_RED" "$C_BOLD" "$*" "$C_RESET"; }
hr()   { printf '%s%s─────────────────────────────────────────────%s\n' "$C_DIM" "" "$C_RESET"; }

# ---------- prereq gates ----------
have() { command -v "$1" >/dev/null 2>&1; }

for tool in curl kubectl; do
  if ! have "$tool"; then err "missing '$tool' - install it first (README Part 0)"; exit 1; fi
done

if ! have python3; then
  warn "python3 not found - a few JSON reads will degrade but the core demo still runs"
fi

# ---------- Vault credentials ----------
SECRETS_FILE="$REPO_ROOT/monitoring/vault/vault-secrets.env"
email=""; password=""; token=""

load_credentials() {
  if [ -f "$SECRETS_FILE" ]; then
    # shellcheck disable=SC1090
    . "$SECRETS_FILE"
    email="${ADMIN_EMAIL:-}"; password="${ADMIN_PASSWORD:-}"
  fi
  if [ -z "$email" ] || [ -z "$password" ]; then
    warn "Vault secrets not found at $SECRETS_FILE"
    warn "run: docker compose up -d vault-seed"
  fi
}

login() {
  [ -n "$token" ] && return 0
  [ -z "$email" ] || [ -z "$password" ] && return 1
  local resp
  resp="$(curl -sS -m5 -X POST http://localhost:8030/auth/login \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"$email\",\"password\":\"$password\"}")"
  token="$(printf '%s' "$resp" | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["token"])
except Exception: print("")' 2>/dev/null)"
  [ -n "$token" ]
}

# ---------- helpers ----------
health_check() {
  hr; info "FLAGSHIP - platform & fleet health"; hr
  for p in 8001 8002 8003; do
    local code; code="$(curl -sS -m4 -o /dev/null -w '%{http_code}' "http://localhost:$p/health" 2>/dev/null)"
    printf '  %-6s ' "$p"; [ "$code" = 200 ] && ok 'UP' || err "HTTP $code"
  done
  for p in 8010 8020 8030 8040 8050; do
    local code; code="$(curl -sS -m4 -o /dev/null -w '%{http_code}' "http://localhost:$p/health" 2>/dev/null)"
    printf '  %-6s ' "$p"; [ "$code" = 200 ] && ok 'UP' || err "HTTP $code"
  done
  printf '  %-6s ' vault
  curl -sS -m4 http://localhost:8200/v1/sys/health 2>/dev/null | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print("SEALED" if d["sealed"] else ("initialized" if d["initialized"] else "not initialized"))
except Exception: sys.stdout.write("-")' 2>/dev/null | (read -r st; [ -n "$st" ] && ok "$st" || err unreachable)
  for hp in "prom 9090/-/ready" "loki 3100/ready" "tempo 3200/ready"; do
    local name pathport; name="${hp% *}"; pathport="${hp#* }"
    local code; code="$(curl -sS -m4 -o /dev/null -w '%{http_code}' "http://localhost:$pathport" 2>/dev/null)"
    printf '  %-6s ' "$name"; [ "$code" = 200 ] && ok 'ready' || err "HTTP $code"
  done
  echo ""
  info "Kubernetes fleet & HPA"
  kubectl get deployments,hpa -n default 2>/dev/null || err "cannot reach k3d cluster"
  echo ""
  info "Prometheus scrape targets"
  local raw up down
  raw="$(curl -sS -m4 http://localhost:9090/api/v1/targets 2>/dev/null)"
  up="$(printf '%s' "$raw" | python3 -c 'import json,sys
try:
  d=json.load(sys.stdin)["data"]["activeTargets"]
  print(sum(1 for t in d if t["health"]=="up"))
except Exception: print("N/A")')"
  down="$(printf '%s' "$raw" | python3 -c 'import json,sys
try:
  d=json.load(sys.stdin)["data"]["activeTargets"]
  print(len([t for t in d if t["health"]!="up"]))
except Exception: print(0)')"
  if [ "$down" = 0 ]; then
    printf '  %-6s ' "7 total"; ok "$up UP, 0 down"
  else
    printf '  %-6s ' "7 total"; warn "$up UP, $down down"
  fi
}

pick_service() { # pick the service that hasn't been remediated most recently
  local out service
  if login; then
    out="$(curl -sS -m5 http://localhost:8030/incidents/current?minutes=120 -H "Authorization: Bearer $token" 2>/dev/null)"
    service="$(printf '%s' "$out" | python3 -c 'import json,sys
from collections import defaultdict
try:
    inc=json.load(sys.stdin).get("incidents",[])
    last=defaultdict(lambda: "1970-01-01")
    for i in inc:
        if i["service_name"] in ("auth-service","payment-service","inventory-service"):
            last[i["service_name"]]=max(last[i["service_name"]], i["action_started_at"] or "")
    import datetime
    oldest=sorted(last.items(), key=lambda kv: kv[1])[0][0] if last else "payment-service"
    print(oldest)
except Exception: print("payment-service")')"
  fi
  echo "${service:-payment-service}"
}

show_anomalies() {
  local service="$1" attempts="${2:-12}" min="${3:-2}"
  info "Watching the anomaly feed on $service (trigger needs $min anomalies in 45s)..."
  for ((i=1; i<=attempts; i++)); do
    sleep 5
    local resp
    resp="$(curl -sS -m5 "http://localhost:8020/anomalies/current?minutes=5" -H "Authorization: Bearer $token" 2>/dev/null)"
    local count conf
    count="$(printf '%s' "$resp" | SERVICE="$service" python3 -c 'import json,sys,os
try:
    hit=[a for a in json.load(sys.stdin).get("anomalies",[]) if a.get("service_name")==os.environ.get("SERVICE")]
    print(len(hit))
except Exception:
    print(0)')"
    if [ "$count" -ge "$min" ]; then
      conf="$(printf '%s' "$resp" | SERVICE="$service" python3 -c 'import json,sys,os
try:
    hit=[a for a in json.load(sys.stdin).get("anomalies",[]) if a.get("service_name")==os.environ.get("SERVICE")]
    print("(confidence %.2f)" % max(a.get("confidence",0) for a in hit) if hit else "")
except Exception:
    print("")')"
      ok "$count anomaly(ies) detected on $service $conf"
      return 0
    fi
    printf '  %s' "$C_DIM.Waiting for detection${i}/${attempts}...$C_RESET"; printf '\r'
  done
  printf '\n'; warn "no anomaly seen within ${attempts}x5s (chaos may have ended, or cooldown still active)"
  return 1
}

service_port() { # host port for a monitored service (k3d NodePort)
  case "$1" in
    auth-service)      echo 8001 ;;
    payment-service)   echo 8002 ;;
    inventory-service) echo 8003 ;;
    *)                 echo 8002 ;;
  esac
}

show_hpa() {
  local service="$1"
  info "HPA replicas (expect $service to autoscale on high CPU)..."
  for i in {1..24}; do
    local repl max pct
    repl="$(kubectl get hpa "$service-hpa" -n default -o jsonpath='{.status.currentReplicas}' 2>/dev/null)"
    max="$(kubectl get hpa "$service-hpa" -n default -o jsonpath='{.spec.maxReplicas}' 2>/dev/null)"
    pct="$(kubectl get hpa "$service-hpa" -n default -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null)"
    printf '  %s\r' "$C_DIM cpu ${pct:-?}%  replicas ${repl:-?}/${max:-?}$C_RESET"
    [ "${repl:-1}" -ge 2 ] && { sleep 2; echo ""; ok "autoscaler grew $service to $repl replicas"; return 0; }
    sleep 6
  done
  echo ""
  warn "HPA did not scale within ~2.5min - metrics-server may be catching up; check 'kubectl top pods'"
}

show_incidents() {
  local service="$1" attempts="${2:-18}" start_label="$3"
  info "Incident lifecycle for $service (expect pending -> resolved/prevented)..."
  local previous=""
  for ((i=1; i<=attempts; i++)); do
    sleep 10
    local resp
    resp="$(curl -sS -m5 "http://localhost:8030/incidents/current?minutes=20" -H "Authorization: Bearer $token" 2>/dev/null)"
    local line
    line="$(printf '%s' "$resp" | python3 -c 'import json,sys
try:
    inc=[x for x in json.load(sys.stdin).get("incidents",[]) if x.get("service_name")=="'"$service"'"]
    newest=max(inc, key=lambda x: x.get("action_started_at") or "") if inc else None
    if newest:
        verdict=newest.get("verdict") or ""
        extra=(" verdict="+verdict) if verdict else ""
        print("#%s %s -> %s (%s)%s" % (newest["id"], newest["action_taken"], newest["outcome"], newest["incident_type"], extra))
    else:
        print("no incidents yet")
except Exception: print("no incidents yet")')"
    if [ "$line" != "$previous" ]; then printf '  %s\n' "$C_DIM$line$C_RESET"; previous="$line"; fi
    case "$line" in
      *"resolved"*)  ok "$start_label incident resolved - self-healing verified"; return 0 ;;
      *"prevented"*) ok "$start_label incident PREVENTED - forecast was right, counterfactual proven"; return 0 ;;
      *"escalated"*) warn "incident escalated - needs human attention"; return 1 ;;
      *"failed"*)    warn "remediation action failed (see decision-engine logs)"; return 1 ;;
    esac
  done
  warn "incident still pending after ~3min - check decision-engine logs"
  return 1
}

# ====================================================================
# reset_detection: give the demo a CLEAN detection baseline.
# The anomaly-detector scores against trailing windows (~20 min) and an
# IsolationForest retrained every 5 min. If demo spikes happened recently,
# that baseline is polluted and new spikes score below the engine's
# confidence threshold. Purging the recent window + restarting the
# detector gives each demo a fresh, honest start.
# ====================================================================
reset_detection() {
  info "Resetting the detection baseline for a clean demo window..."
  docker compose exec -T postgres \
    psql -U cloudguardian -d cloudguardian -q -v ON_ERROR_STOP=1 \
    -c "DELETE FROM anomalies WHERE detected_at > now() - interval '2 hours';" \
    -c "DELETE FROM metric_readings WHERE service_name IN ('auth-service','payment-service','inventory-service') AND recorded_at > now() - interval '35 minutes';" \
    -c "DELETE FROM forecasts WHERE generated_at > now() - interval '30 minutes';"
  docker compose restart anomaly-detector >/dev/null 2>&1
  local ok_code
  for i in $(seq 1 15); do
    ok_code="$(curl -sS -m3 -o /dev/null -w '%{http_code}' http://localhost:8020/health 2>/dev/null)"
    [ "$ok_code" = 200 ] && { ok "fresh baseline ready"; return 0; }
    sleep 2
  done
  warn "anomaly-detector did not come back healthy - proceeding anyway"
  return 0
}

# ====================================================================
# demo_reactive: inject a REAL cpu spike (busy-loop) and watch the
# system detect -> restart (k8s rollout) -> verify -> HPA autoscale.
# ====================================================================
demo_reactive() {
  load_credentials
  login || { err "login failed - check monitoring/vault/vault-secrets.env"; return 1; }

  local service duration
  local service port
  service="$(pick_service)"
  port="$(service_port "$service")"
  duration="${1:-120}"
  reset_detection
  hr; info "REACTIVE DEMO - chaos on $service (cpu_spike, ${duration}s, host port $port)"
  hr

  info "Injecting fault fleet-wide via decision-engine (cpu_spike, ${duration}s)..."
  curl -sS -m10 -X POST "http://localhost:8030/chaos/$service/cpu_spike?duration_seconds=$duration" \
    -H "Authorization: Bearer $token" || true
  echo ""

  show_anomalies "$service" 15 2 || return 1
  show_hpa "$service"
  show_incidents "$service" 18 "REACTIVE ($(basename "$0"))"
}

# ====================================================================
# demo_predictive: memory_leak -> forecast-engine predicts breach
# -> decision-engine acts BEFORE the SLO is breached.
# ====================================================================
demo_predictive() {
  load_credentials
  login || { err "login failed - check monitoring/vault/vault-secrets.env"; return 1; }

  local service port
  service="$(pick_service)"
  port="$(service_port "$service")"
  reset_detection
  hr; info "PREDICTIVE DEMO - forecast a breach on $service, act before it happens"
  hr

  info "Injecting memory_leak fleet-wide into $service (quiet, slow - the interesting thing is the forecast)"
  curl -sS -m10 -X POST "http://localhost:8030/chaos/$service/memory_leak?duration_seconds=360" \
    -H "Authorization: Bearer $token" >/dev/null 2>&1 || true

  info "Waiting for the forecast-engine to retrain and flag a breach risk..."
  local risk="0" eta="5"
  for ((i=1; i<=24; i++)); do
    sleep 10
    local resp
    resp="$(curl -sS -m5 http://localhost:8040/forecast/breach-risk -H "Authorization: Bearer $token" 2>/dev/null)"
    risk="$(printf '%s' "$resp" | python3 -c 'import json,sys
try:
    r=[x for x in json.load(sys.stdin).get("risks",[]) if x.get("service")=="'"$service"'"]
    print("%.2f" % max(x.get("breach_risk",0) for x in r) if r else "0")
except Exception: print("0")')"
    eta="$(printf '%s' "$resp" | python3 -c 'import json,sys
try:
    r=[x for x in json.load(sys.stdin).get("risks",[]) if x.get("service")=="'"$service"'"]
    print("%.1f" % max(x.get("eta_minutes",5) for x in r) if r else "5")
except Exception: print("5")')"
    printf '  %s\r' "$C_DIM breach_risk=$risk (threshold 0.80)${i}/24$C_RESET"
    if [ "$(python3 -c "print(int($risk >= 0.80))" 2>/dev/null)" = "1" ]; then
      echo ""; ok "forecast crossed the threshold (risk=$risk, eta=${eta}min) - predictive action should fire"
      local attempts
      attempts="$(python3 -c "import math; print(min(50, int(math.ceil($eta*6)) + 12))" 2>/dev/null)"
      [ -z "$attempts" ] && attempts=42
      show_incidents "$service" "$attempts" "PREDICTIVE ($(basename "$0"))"
      return $?
    fi
  done
  echo ""
  warn "breach risk stayed below 0.80 within ~4min (forecast needs a few retrain cycles)"
  warn "check http://localhost:3001 Phase 7 panel, or run demo_predictive again"
}

# ====================================================================
# demo_full: ONE command that proves the entire self-healing loop,
# reactive AND predictive, and then prints the incident ledger so the
# run can be pasted straight into the report.
# ====================================================================
demo_full() {
  load_credentials
  login || { err "login failed - check monitoring/vault/vault-secrets.env"; return 1; }

  hr; info "FULL-CYCLE DEMO - reactive heal + predictive preemption + counterfactual proof"
  hr

  demo_reactive "${1:-90}" || warn "reactive leg did not complete cleanly"
  echo ""
  demo_predictive || warn "predictive leg did not complete cleanly"
  echo ""

  hr; info "INCIDENT LEDGER (report capture) - last 3 incidents per service"
  hr
  for s in auth-service payment-service inventory-service; do
    printf '%s\n' "→ $s"
    curl -sS -m5 "http://localhost:8030/incidents/history?service=$s" -H "Authorization: Bearer $token" 2>/dev/null \
      | python3 -c 'import json,sys
try:
    inc=json.load(sys.stdin).get("incidents",[])[:3]
    for i in inc:
        v=i.get("verdict") or ""
        s=i.get("verification_json")
        peak=""
        if s:
            try: peak=" peak=%s/%s" % (round(s.get("actual_peak_value",0),1), s.get("threshold_value"))
            except Exception: pass
        print("  #%s %-8s -> %-10s %-10s %s%s" % (i["id"], i["incident_type"], i["outcome"], i["action_taken"][:14], v, peak))
except Exception:
    print("  (no incidents yet)")'
  done
}

# ====================================================================
# observability: prove logs (Loki) + traces (Tempo) + dashboards (Grafana)
# ====================================================================
observability() {
  hr; info "OBSERVABILITY - logs in Loki, traces in Tempo, datasources in Grafana"
  hr

  info "Loki: latest decision-engine log lines..."
  curl -sS -m8 "http://localhost:3100/loki/api/v1/query_range" \
    --data-urlencode 'query={container="decision-engine"}' --data-urlencode 'limit=3' \
    -G 2>/dev/null | python3 -c 'import json,sys
try:
  r=json.load(sys.stdin)["data"]["result"]
  for s in r:
    msg=s["values"][-1][1]
    print("  ",msg[:110])
except Exception:
  print("   (no log lines yet)")'

  echo ""
  info "Tempo: decision-engine traces (remediation spans)..."
  curl -sS -m8 "http://localhost:3200/api/search?limit=5&service.name=decision-engine" 2>/dev/null \
    | python3 -c 'import json,sys
try:
  tr=json.load(sys.stdin).get("traces",[])
  if not tr: print("   (no traces in the search window - run the REACTIVE demo to generate new ones)")
  for t in tr[:5]: print("   %-28s %s ms  %s" % (t.get("rootTraceName","?"), t.get("durationMs","?"), t.get("traceID","")[:12]))
except Exception:
  print("   (tempo not reachable)")'

  echo ""
  info "Grafana datasources (Prometheus + Loki + Tempo should all be listed)..."
  curl -sS -m5 -u admin:admin http://localhost:3000/api/datasources 2>/dev/null \
    | python3 -c 'import json,sys
try: print("   " + ", ".join(d["name"] for d in json.load(sys.stdin)))
except Exception: print("   (grafana not reachable)")'
}

open_dashboards() {
  info "Opening Grafana (http://localhost:3000) and Mission Control (http://localhost:3001)..."
  for url in "http://localhost:3000" "http://localhost:3001"; do
    if have xdg-open; then (xdg-open "$url" >/dev/null 2>&1 &)
    elif have open; then (open "$url" >/dev/null 2>&1 &)
    else echo "  open manually: $url"; fi
  done
}

# ====================================================================
menu() {
  while true; do
    hr
    printf '%sCloudGuardian AI - Live Demo%s\n' "$C_BOLD" "$C_RESET"
    printf '  %s1)%s  Status - platform / fleet / observability summary\n' "$C_CYAN" "$C_RESET"
    printf '  %s2)%s  REACTIVE demo - inject cpu_spike, watch detect -> heal -> HPA\n' "$C_CYAN" "$C_RESET"
    printf '  %s3)%s  PREDICTIVE demo - memory leak, forecast fires before the breach\n' "$C_CYAN" "$C_RESET"
    printf '  %s4)%s  Observability - Loki logs, Tempo traces, Grafana\n' "$C_CYAN" "$C_RESET"
    printf '  %s5)%s  Open dashboards (Grafana + Mission Control)\n' "$C_CYAN" "$C_RESET"
    printf '  %s6)%s  FULL cycle - reactive + predictive + counterfactual verdict, with report ledger\n' "$C_CYAN" "$C_RESET"
    printf '  %s0)%s  Exit\n' "$C_YEL" "$C_RESET"
    hr
    read -r -p "choose: " choice || break
    case "$choice" in
      1) health_check ;;
      2) demo_reactive ;;
      3) demo_predictive ;;
      4) observability ;;
      5) open_dashboards ;;
      6) demo_full ;;
      0) break ;;
      *) warn "invalid choice" ;;
    esac
    echo ""
  done
}

case "${1:-menu}" in
  --status|status)      load_credentials; login; health_check ;;
  --all|all)            load_credentials; login; health_check; echo ""; demo_reactive "${2:-120}"; observability ;;
  --reactive)           load_credentials; login; demo_reactive "${2:-120}" ;;
  --predictive)         load_credentials; login; demo_predictive ;;
  --full|full)          load_credentials; login; demo_full "${2:-90}" ;;
  --observability)      observability ;;
  *)                    load_credentials; menu ;;
esac