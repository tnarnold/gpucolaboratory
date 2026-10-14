#!/usr/bin/env bash
# Runs INSIDE the Colab VM (as root). Called by `colab-llm up`.
# Installs Tailscale (userspace networking) + Ollama, joins the tailnet and pulls the models.
# Lines starting with COLAB_LLM_ are parsed by the local script.
set -euo pipefail

ENV_FILE=${1:-/content/colab-llm.env}
# shellcheck disable=SC1090
source "$ENV_FILE"
rm -f "$ENV_FILE"

log() { echo "[setup] $*"; }
export DEBIAN_FRONTEND=noninteractive

log "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'nenhuma')"

log "Instalando dependências"
apt-get -qq update >/dev/null 2>&1 || true
apt-get -qq install -y zstd pciutils lshw curl >/dev/null

# ---------------------------------------------------------------- Tailscale
if ! command -v tailscale >/dev/null; then
  log "Instalando Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh >/var/log/tailscale-install.log 2>&1
fi

mkdir -p /run/tailscale
if ! pgrep -x tailscaled >/dev/null; then
  # Colab has no /dev/net/tun -> userspace networking. `--state=mem:` registers the node as
  # ephemeral, so it disappears from the tailnet shortly after the VM dies.
  setsid nohup tailscaled --tun=userspace-networking --state=mem: \
    --socket=/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 </dev/null &
  sleep 3
fi

TS_ARGS=(--hostname="$TS_HOSTNAME" --accept-dns=false)
[[ -n "${TS_TAGS:-}" ]] && TS_ARGS+=(--advertise-tags="$TS_TAGS")

if tailscale status >/dev/null 2>&1; then
  log "Tailscale já conectado"
elif [[ -n "${TS_AUTHKEY:-}" ]]; then
  log "Conectando na tailnet com auth key"
  tailscale up --authkey="$TS_AUTHKEY" "${TS_ARGS[@]}"
else
  log "Sem auth key: login interativo"
  setsid nohup tailscale up "${TS_ARGS[@]}" >/var/log/tailscale-up.log 2>&1 </dev/null &
  url=""
  for _ in $(seq 60); do
    url=$(grep -oE 'https://login\.tailscale\.com/[^[:space:]]+' /var/log/tailscale-up.log | head -n1 || true)
    [[ -n "$url" ]] && break
    sleep 1
  done
  echo "COLAB_LLM_TS_LOGIN_URL=${url:-<não encontrado, veja /var/log/tailscale-up.log>}"
fi
unset TS_AUTHKEY

# ---------------------------------------------------------------- Ollama
if ! command -v ollama >/dev/null; then
  log "Instalando Ollama"
  curl -fsSL https://ollama.com/install.sh | sh >/var/log/ollama-install.log 2>&1
fi

cat >/etc/colab-llm-ollama.env <<EOF
export OLLAMA_HOST=0.0.0.0:${OLLAMA_PORT}
export OLLAMA_ORIGINS='*'
export OLLAMA_KEEP_ALIVE=-1
export OLLAMA_CONTEXT_LENGTH=${OLLAMA_CONTEXT_LENGTH}
export OLLAMA_FLASH_ATTENTION=1
EOF

ollama_up() { curl -fsS "http://127.0.0.1:${OLLAMA_PORT}/api/version" >/dev/null 2>&1; }

if ! ollama_up; then
  log "Iniciando Ollama (com watchdog)"
  setsid nohup bash -c 'source /etc/colab-llm-ollama.env; while true; do ollama serve; echo "ollama saiu ($?), reiniciando"; sleep 2; done' \
    >>/var/log/ollama.log 2>&1 </dev/null &
  for _ in $(seq 60); do ollama_up && break; sleep 1; done
fi
ollama_up || { log "Ollama não subiu"; tail -n 30 /var/log/ollama.log; exit 1; }
grep -iE 'inference compute' /var/log/ollama.log | tail -n1 | sed 's/^/[setup] /' || true

# shellcheck disable=SC1091
source /etc/colab-llm-ollama.env
export OLLAMA_HOST=127.0.0.1:${OLLAMA_PORT}
IFS=',' read -ra MODEL_LIST <<<"$MODELS"
for m in "${MODEL_LIST[@]}"; do
  m=$(echo "$m" | xargs)
  [[ -z "$m" ]] && continue
  log "Baixando $m"
  if ! ollama pull "$m" >/var/log/ollama-pull.log 2>&1; then
    log "Falha ao baixar $m:"
    tail -n 3 /var/log/ollama-pull.log | tr '\r' '\n' | tail -n 2
    exit 1
  fi
done
ollama list

# ---------------------------------------------------------------- Summary
for i in $(seq 600); do
  state=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' || true)
  [[ "$state" == "Running" ]] && break
  [[ $i == 1 ]] && log "Aguardando o login no Tailscale..."
  sleep 1
done
echo "COLAB_LLM_TS_STATE=${state:-unknown}"
echo "COLAB_LLM_TS_IP=$(tailscale ip -4 2>/dev/null || true)"
echo "COLAB_LLM_TS_DNS=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' || true)"
log "Pronto"
