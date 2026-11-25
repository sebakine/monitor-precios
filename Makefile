.PHONY: install lint test test-js check revisar grafico build

DB ?= historial.db

install:  ## Instala dependencias de desarrollo
	uv sync

lint:  ## Formato, lint y tipos
	uv run ruff format --check src tests
	uv run ruff check src tests
	uv run mypy

test:  ## Tests con cobertura
	uv run pytest

test-js:  ## Test con Chromium real (necesita el extra js)
	uv sync --extra js
	uv run playwright install chromium
	uv run pytest -m js --no-cov -v

check: lint test

revisar:  ## Consulta los productos de productos.toml una vez
	uv run monitor revisar --db $(DB)

grafico:  ## Dibuja el historial
	uv run monitor grafico --db $(DB) -o historial.png

build:
	uv build
