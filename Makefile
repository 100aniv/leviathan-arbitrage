.PHONY: dev test lint format build up down logs clean install help

PYTHON := python3.12
PIP := pip
DOCKER_COMPOSE := docker compose
ENGINE_DIR := engine

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install engine dependencies
	cd $(ENGINE_DIR) && $(PIP) install -e ".[dev]"

dev: up ## Start development environment (alias for up)

test: ## Run test suite
	cd $(ENGINE_DIR) && pytest tests/ -v

test-unit: ## Run unit tests only
	cd $(ENGINE_DIR) && pytest tests/unit/ -v

test-integration: ## Run integration tests only
	cd $(ENGINE_DIR) && pytest tests/integration/ -v

test-e2e: ## Run end-to-end tests only
	cd $(ENGINE_DIR) && pytest tests/e2e/ -v

test-cov: ## Run tests with coverage report
	cd $(ENGINE_DIR) && pytest tests/ --cov=src --cov-report=html --cov-report=term-missing

lint: ## Run linter (ruff)
	cd $(ENGINE_DIR) && ruff check src/ tests/

format: ## Auto-format code (ruff)
	cd $(ENGINE_DIR) && ruff format src/ tests/ && ruff check --fix src/ tests/

build: ## Build Docker images
	$(DOCKER_COMPOSE) build

up: ## Start all services
	$(DOCKER_COMPOSE) up -d

up-logs: ## Start all services and tail logs
	$(DOCKER_COMPOSE) up

down: ## Stop all services
	$(DOCKER_COMPOSE) down

down-volumes: ## Stop all services and remove volumes
	$(DOCKER_COMPOSE) down -v

logs: ## Tail all service logs
	$(DOCKER_COMPOSE) logs -f

logs-engine: ## Tail engine logs
	$(DOCKER_COMPOSE) logs -f engine

logs-redis: ## Tail Redis logs
	$(DOCKER_COMPOSE) logs -f redis

ps: ## Show service status
	$(DOCKER_COMPOSE) ps

clean: ## Remove build artifacts and cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	find . -name "coverage.xml" -delete 2>/dev/null || true

shell-engine: ## Open shell in engine container
	$(DOCKER_COMPOSE) exec engine bash

shell-redis: ## Open Redis CLI
	$(DOCKER_COMPOSE) exec redis redis-cli

shell-db: ## Open PostgreSQL shell
	$(DOCKER_COMPOSE) exec timescaledb psql -U leviathan -d leviathan

migrate: ## Run database migrations
	cd $(ENGINE_DIR) && alembic upgrade head

migrate-new: ## Create a new migration
	cd $(ENGINE_DIR) && alembic revision --autogenerate -m "$(name)"

backtest: ## Run backtest with synthetic data
	cd $(ENGINE_DIR) && $(PYTHON) -m src.cli.backtest_cli --data synthetic --candles 2000

backtest-optimize: ## Run backtest with walk-forward optimization
	cd $(ENGINE_DIR) && $(PYTHON) -m src.cli.backtest_cli --data synthetic --optimize --trials 50

paper-trade: ## Run 5-minute paper trading session
	cd $(ENGINE_DIR) && $(PYTHON) -m src.cli.paper_runner --duration 300 --report

paper-trade-quick: ## Run 1-minute paper trading session
	cd $(ENGINE_DIR) && $(PYTHON) -m src.cli.paper_runner --duration 60 --report --verbose

.DEFAULT_GOAL := help
