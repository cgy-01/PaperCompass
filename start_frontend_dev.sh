#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${PAPER_FIREHOSE_DATA_DIR:-${PROJECT_DIR}/.runtime-demo}"
PORT="${1:-9000}"
HOST="${PAPER_COMPASS_HOST:-127.0.0.1}"
TEMPLATE="${PROJECT_DIR}/src/paper_firehose/system/templates/dashboard_template.html"
OUTPUT="${DATA_DIR}/html/index.html"
CLI_BIN="/home/cgy/miniconda3/envs/paper-compass/bin/paper-firehose"

if [[ ! "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "错误：端口必须是 1 到 65535 之间的整数。" >&2
  exit 1
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

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"${PROJECT_DIR}/start_dashboard.sh" "${PORT}" &
SERVER_PID=$!

echo
echo "前端开发模式已启动：http://${HOST}:${PORT}"
echo "监听文件：${TEMPLATE}"
echo "保存前端模板后会自动重建；浏览器刷新即可查看。"
echo "按 Ctrl+C 停止服务。"
echo

last_signature="$(stat -c '%Y:%s' "${TEMPLATE}")"

while kill -0 "${SERVER_PID}" 2>/dev/null; do
  sleep 1
  signature="$(stat -c '%Y:%s' "${TEMPLATE}")"
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
