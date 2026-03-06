# LEVIATHAN — Global Arbitrage Engine

A high-frequency cross-exchange arbitrage engine built on Python 3.12+ with a hexagonal architecture.

## Architecture

```
engine/src/
├── core/          # Domain models, shared types
├── strategies/    # Arbitrage strategy implementations
├── execution/     # Order execution, cross-exchange atomic protocol
├── infra/
│   ├── exchange/  # Exchange adapters (Binance, OKX, Bybit, ...)
│   ├── redis/     # Redis Streams publisher/consumer, WAL dual-write
│   └── db/        # PostgreSQL schema, async queries, migrations
├── risk/          # Risk Guardian, kill switch, position limits
└── friction/      # Fee model, slippage model, friction filter
```

## Quick Start

```bash
cp .env.example .env
# Fill in API keys and settings
make up          # Start all services (Docker Compose)
make install     # Install Python dependencies
make test        # Run test suite
```

## Services

| Service        | Port  | Description                    |
|----------------|-------|--------------------------------|
| engine         | 8000  | REST API + Prometheus metrics  |
| engine         | 8001  | WebSocket (signals, positions) |
| redis          | 6379  | Redis 7 (Streams + Cache)      |
| timescaledb    | 5432  | TimescaleDB / PostgreSQL 16    |
| dashboard      | 3000  | Next.js monitoring dashboard   |
| prometheus     | 9090  | Metrics scraper                |
| grafana        | 3001  | Dashboards                     |
| redis-exporter | 9121  | Redis Prometheus exporter      |

## Development

```bash
make test-unit         # Unit tests (no external services)
make test-integration  # Integration tests (requires Docker services)
make lint              # Ruff linter
make format            # Auto-format with ruff
make logs              # Tail all service logs
make shell-redis       # Redis CLI
make shell-db          # PostgreSQL shell
```

## Phase Gates

- **Alpha**: $70/exchange, validates core execution with minimal capital
- **Beta**: $750/exchange, 200+ round-trips, 72h+ runtime for statistical validation
- **Production**: Full capital deployment post-Beta gate verification
