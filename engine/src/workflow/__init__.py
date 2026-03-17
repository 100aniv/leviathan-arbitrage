"""LEVIATHAN 워크플로우 자동화 레이어.

순수 Python 유틸리티: 체크포인팅, 일관성 검사, 상태 검증.
기존 SSOT.md + leviathan.md + OMC 오케스트레이션을 보조하는 역할.

DB 분리:
  - TimescaleDB (Docker) = 거래 데이터, PnL, 메트릭 → engine/src/
  - SQLite (로컬 .omc/) = 워크플로우 체크포인트 → engine/src/workflow/ 전용
"""
