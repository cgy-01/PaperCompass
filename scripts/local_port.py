#!/usr/bin/env python3
"""Avoid Windows-reserved localhost ports when launching WSL development tools."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys


def parse_exclusions(output):
    # netsh headers are localized; numeric range rows have the same format.
    ranges = []
    for line in output.decode('ascii', errors='ignore').splitlines():
        match = re.fullmatch(r'\s*(\d+)\s+(\d+)\s*\*?\s*', line)
        if match:
            start, end = map(int, match.groups())
            if 1 <= start <= end <= 65535:
                ranges.append((start, end))
    return ranges


def available(port):
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


def select_port(port, ranges, *, explicit=False, can_bind=available):
    blocked = next(((start, end) for start, end in ranges if start <= port <= end), None)
    if not blocked:
        return port
    reason = f'Windows 已保留端口 {port}（范围 {blocked[0]}–{blocked[1]}）'
    if explicit:
        raise ValueError(reason + '，请改用其他端口，例如 8080 或 18080。')
    for candidate in (8080, 18080, 5173, 3000):
        if not any(start <= candidate <= end for start, end in ranges) and can_bind(candidate):
            print(reason + f'，本次自动改用 {candidate}。', file=sys.stderr)
            return candidate
    raise ValueError(reason + '，没有找到可用的替代端口，请手动指定。')


def main():
    port = int(sys.argv[1])
    if not 1 <= port <= 65535:
        raise ValueError('端口必须介于 1 和 65535 之间')
    kernel = Path('/proc/sys/kernel/osrelease')
    ranges = []
    if kernel.exists() and 'microsoft' in kernel.read_text().casefold():
        netsh = shutil.which('netsh.exe') or '/mnt/c/Windows/System32/netsh.exe'
        try:
            result = subprocess.run([netsh, 'interface', 'ipv4', 'show', 'excludedportrange',
                                     'protocol=tcp'], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=5, check=True)
            ranges = parse_exclusions(result.stdout)
        except (OSError, subprocess.SubprocessError):
            print('提示：未能检测 Windows 保留端口，请以浏览器实际连通情况为准。', file=sys.stderr)
    print(select_port(port, ranges, explicit=sys.argv[2] == 'explicit'))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, IndexError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
