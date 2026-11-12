#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${PAPER_FIREHOSE_DATA_DIR:-${PROJECT_DIR}/.runtime-demo}"
PORT="${1:-8765}"
HOST="${PAPER_COMPASS_HOST:-127.0.0.1}"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
CLI_BIN="${PROJECT_DIR}/.venv/bin/paper-firehose"

if [[ ! "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "错误：端口必须是 1 到 65535 之间的整数。" >&2
  echo "示例：./start_dashboard.sh 8765" >&2
  exit 1
fi

# Prefer this project's Miniconda environment, falling back to a local venv.
if [[ -x "/home/cgy/miniconda3/envs/paper-compass/bin/python" ]]; then
  PYTHON_BIN="/home/cgy/miniconda3/envs/paper-compass/bin/python"
  CLI_BIN="/home/cgy/miniconda3/envs/paper-compass/bin/paper-firehose"
fi

if [[ ! -x "${PYTHON_BIN}" || ! -x "${CLI_BIN}" ]]; then
  echo "错误：没有找到 paper-compass 项目的 Python 环境。" >&2
  echo "预期位置：/home/cgy/miniconda3/envs/paper-compass" >&2
  exit 1
fi

CONFIG_PATH="${DATA_DIR}/config/config.yaml"
if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "错误：没有找到配置文件 ${CONFIG_PATH}" >&2
  exit 1
fi

export PAPER_FIREHOSE_DATA_DIR="${DATA_DIR}"

echo "正在启动 Paper Compass 只读应用…"
echo "监听地址：http://${HOST}:${PORT}"
echo "按 Ctrl+C 停止服务。"
echo

exec "${CLI_BIN}" --config "${CONFIG_PATH}" serve --host "${HOST}" --port "${PORT}"
