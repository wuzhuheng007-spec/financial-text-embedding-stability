"""Thin research workflow layer: registered datasets, registered factors, recorded runs.

Runs execute in a separate job container (systemd timer -> `worker --once`), never in a web
request. Every run writes to its own directory and is never overwritten.
"""
