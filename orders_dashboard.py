"""Ozon Dashboard — thin entry point.

All logic has been moved to src/dashboard/ modules.
This file exists for backwards compatibility with run_dashboard.cmd/ps1.
"""
from src.dashboard.app import create_app

__all__ = ["create_app"]
