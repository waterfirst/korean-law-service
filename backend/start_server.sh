#!/bin/bash
# 법률 도우미 API 서버 자동 시작
cd /home/ubuntu/.cokacdir/workspace/pfiuywu4/korean-law-service/backend

# 이미 실행 중이면 스킵
if pgrep -f "python3 server.py" > /dev/null; then
    echo "법률 도우미 서버 이미 실행 중"
    exit 0
fi

nohup python3 server.py > /tmp/law_server.log 2>&1 &
echo "법률 도우미 서버 시작 (PID: $!)"
