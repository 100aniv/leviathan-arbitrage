#!/bin/bash
# Phase K P-01 완료 처리 스크립트 (2H 후 실행)
# 사용: bash scripts/paper_complete_p01.sh

cd /Users/100aniv/Development/arbitrage_OMC/engine

JWT_SECRET="I51knannQ0JhgDBIYDqC9jFVIkE-g0w0Dw5EQq1kSd6mfw97U1JM6kcvWH6Sc-QslJxKDSDoS39FNhxM0Mpfiw" python -c "
import urllib.request, json, sys, os
sys.path.insert(0, 'src')
from src.api.auth import create_token
token = create_token('admin')

# POST /api/paper/complete
req = urllib.request.Request(
    'http://localhost:8000/api/paper/complete',
    data=json.dumps({
        'session_id': 'd0703d15',
        'exchange_id': 'binance',
        'strategy_id': 'funding_rate_v1',
        'duration_hours': 2.0,
    }).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'},
    method='POST'
)
with urllib.request.urlopen(req, timeout=10) as resp:
    print('P-01 완료:', json.dumps(json.loads(resp.read()), indent=2, ensure_ascii=False))
"
