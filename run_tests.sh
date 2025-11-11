#!/bin/bash
# Script to run tests and linting with uvx without local dependency management

set -e  # Exit on first error

curl -L -o ./.github/linters/pyproject.toml https://raw.githubusercontent.com/home-assistant/core/refs/heads/dev/pyproject.toml

uvx ruff check --config .github/linters/.ruff.toml main.py
uvx ruff check --config .github/linters/.ruff.toml xcomfort/
uvx --with aiohttp --with rx --with pycryptodome --with pytest-asyncio pytest tests/ -v
