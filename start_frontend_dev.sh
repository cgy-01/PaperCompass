#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${PAPER_FIREHOSE_DATA_DIR:-${PROJECT_DIR}/.runtime-demo}"
PORT="${1:-9000}"
HOST="${PAPER_COMPASS_HOST:-127.0.0.1}"
# WSL localhost forwarding can fail independently of the application. Bind the
# development server to its network interface so Windows can also use its IP.
if [[ -z "${PAPER_COMPASS_HOST:-}" ]] && grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  HOST="0.0.0.0"
fi
TEMPLATE="${PROJECT_DIR}/src/paper_firehose/system/templates/dashboard_template.html"
FRONTEND="${PROJECT_DIR}/src/paper_firehose/system/frontend"
OUTPUT="${DATA_DIR}/html/index.html"
CLI_BIN="/home/cgy/miniconda3/envs/paper-compass/bin/paper-firehose"

if [[ ! "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "错误：端口必须是 1 到 65535 之间的整数。" >&2
  exit 1
fi

PORT_MODE=default
if (( $# > 0 )); then PORT_MODE=explicit; fi
PORT="$(python3 "${PROJECT_DIR}/scripts/local_port.py" "${PORT}" "${PORT_MODE}")"

# This script is for local development. Production systemd runs `serve`
# directly and never enables the development account implicitly.
export PAPER_COMPASS_DEV_LOGIN="${PAPER_COMPASS_DEV_LOGIN:-1}"
if [[ "${PAPER_COMPASS_DEV_LOGIN}" == "1" ]]; then
  if [[ -n "${PAPER_COMPASS_PUBLIC_URL:-}" && "${PAPER_COMPASS_PUBLIC_URL}" != "http://127.0.0.1:${PORT}" ]]; then
    echo "错误：开发账号不能使用非本地站点地址。请先 unset PAPER_COMPASS_PUBLIC_URL，或设置 PAPER_COMPASS_DEV_LOGIN=0。" >&2
    exit 1
  fi
  HOST="127.0.0.1"
  export PAPER_COMPASS_PUBLIC_URL="http://127.0.0.1:${PORT}"
  export PAPER_COMPASS_EVENTS_ENABLED="${PAPER_COMPASS_EVENTS_ENABLED:-0}"
  unset GITHUB_CLIENT_ID GITHUB_CLIENT_SECRET
  echo "本地开发账号已启用：local-developer（收藏与已读使用独立开发用户库）"
fi

if [[ ! -x "${CLI_BIN}" ]]; then
  CLI_BIN="${PROJECT_DIR}/.venv/bin/paper-firehose"
fi

if [[ ! -x "${CLI_BIN}" ]]; then
  echo "错误：没有找到 paper-compass 命令行环境。" >&2
  exit 1
fi

if [[ ! -f "${DATA_DIR}/config/config.yaml" ]]; then
  echo "错误：没有找到 ${DATA_DIR}/config/config.yaml" >&2
  exit 1
fi

if [[ ! -f "${TEMPLATE}" ]]; then
  echo "错误：没有找到前端模板 ${TEMPLATE}" >&2
  exit 1
fi

export PAPER_FIREHOSE_DATA_DIR="${DATA_DIR}"
export PAPER_COMPASS_HOST="${HOST}"

# Fail before polling: otherwise an older server could satisfy the health check.
"$(dirname -- "${CLI_BIN}")/python" - "${HOST}" "${PORT}" <<'PY'
import socket
import sys

try:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((sys.argv[1], int(sys.argv[2])))
except OSError as exc:
    print(f"无法监听 {sys.argv[1]}:{sys.argv[2]}：{exc}。请停止旧服务或换一个端口。", file=sys.stderr)
    sys.exit(1)
PY

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

trap 'exit 130' INT
trap 'exit 143' TERM

"${PROJECT_DIR}/start_dashboard.sh" "${PORT}" &
SERVER_PID=$!

CHECK_HOST="${HOST}"
if [[ "${HOST}" == "0.0.0.0" ]]; then
  CHECK_HOST="127.0.0.1"
fi
ready=false
for (( attempt=0; attempt<30; attempt++ )); do
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    wait "${SERVER_PID}"
    exit 1
  fi
  if curl --noproxy '*' --fail --silent --max-time 1 \
    "http://${CHECK_HOST}:${PORT}/api/health" | grep -q '"ready"'; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "${ready}" != true ]]; then
  echo "错误：服务未在 30 秒内就绪，请检查上面的启动日志。" >&2
  exit 1
fi

echo
echo "前端开发模式已就绪：http://${CHECK_HOST}:${PORT}"
if [[ "${HOST}" == "0.0.0.0" ]]; then
  read -r -a LOCAL_IPS <<< "$(hostname -I)"
  if [[ ${#LOCAL_IPS[@]} -gt 0 ]]; then
    echo "Windows 浏览器若无法访问 localhost，请打开：http://${LOCAL_IPS[0]}:${PORT}"
  fi
  echo "开发服务监听所有网卡，请仅在可信网络使用。"
fi
echo "监听文件：${TEMPLATE}"
echo "保存前端模板后会自动重建；浏览器刷新即可查看。"
echo "按 Ctrl+C 停止服务。"
echo

frontend_signature() {
  find "${TEMPLATE}" "${FRONTEND}" -type f -printf '%p:%T@:%s\n' | sort | sha256sum
}
last_signature="$(frontend_signature)"

while kill -0 "${SERVER_PID}" 2>/dev/null; do
  sleep 1
  signature="$(frontend_signature)"
  if [[ "${signature}" != "${last_signature}" ]]; then
    last_signature="${signature}"
    if "${CLI_BIN}" \
      --config "${DATA_DIR}/config/config.yaml" \
      dashboard --output "${OUTPUT}"; then
      echo "[$(date '+%H:%M:%S')] 前端已更新，请刷新浏览器。"
    else
      echo "[$(date '+%H:%M:%S')] 前端生成失败，服务继续运行。" >&2
    fi
  fi
done

wait "${SERVER_PID}"
