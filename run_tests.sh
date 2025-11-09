#!/bin/bash
# Script to run tests with uvx without local dependency management
uvx --with aiohttp --with rx --with pycryptodome --with pytest-asyncio pytest tests/ -v
