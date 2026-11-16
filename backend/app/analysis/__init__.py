"""Fixture analysis: point-in-time database reads -> calculation engine -> decision layer.

Shared by the session run (jobs) and the backtest: both analyse a fixture "as known at as_of" through
exactly the same code, which is what makes a backtest honest (ARCHITECTURE.md 8.5).
"""
