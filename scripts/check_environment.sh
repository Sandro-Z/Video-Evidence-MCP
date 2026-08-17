#!/bin/sh
set -eu

uname -a
python3 --version || true
docker --version || true
docker compose version || true
ffmpeg -version 2>/dev/null | head -n 1 || true
lscpu | sed -n '1,20p'
free -h
df -h . /tmp
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || true
