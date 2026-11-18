#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_EXPORTS="$(python3 "${PROJECT_DIR}/src/paper_firehose/core/environment.py" --shell)"
eval "${ENV_EXPORTS}"
unset ENV_EXPORTS
DATA_DIR="${PAPER_FIREHOSE_DATA_DIR:-${PROJECT_DIR}/.runtime-demo}"
CLI_BIN="/home/cgy/miniconda3/envs/paper-compass/bin/paper-firehose"
CONFIG_PATH="${DATA_DIR}/config/config.yaml"

if [[ ! -x "${CLI_BIN}" ]]; then
  echo "错误：没有找到项目的 Miniconda 环境：${CLI_BIN}" >&2
  exit 1
fi

echo "开始补全会议作者与摘要；过程可安全中断，重新运行会从缺失记录继续。"
PAPER_FIREHOSE_DATA_DIR="${DATA_DIR}" "${CLI_BIN}" --config "${CONFIG_PATH}" \
  enrich-conferences --workers 16 "$@"
PAPER_FIREHOSE_DATA_DIR="${DATA_DIR}" "${CLI_BIN}" --config "${CONFIG_PATH}" dashboard
echo "补全完成，页面已重新生成。"
