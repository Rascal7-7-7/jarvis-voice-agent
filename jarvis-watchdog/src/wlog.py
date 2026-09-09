"""Append-only event log with size-based rotation. Writes inside this project only.

Rotation is deliberately the simplest thing that cannot lose the current file:
when the log passes the cap it is renamed to `.1` (replacing any previous `.1`)
and a new one starts. One generation is kept. No timers, no external tool, no
compression -- a watchdog whose logging can fail is a watchdog that can fail.
"""
from __future__ import annotations

import json
import os
import time

MAX_BYTES = 1 * 1024 * 1024
KEEP_GENERATIONS = 1


class WatchdogLog:
    def __init__(self, path: str, max_bytes: int = MAX_BYTES):
        self.path = os.path.abspath(path)
        self.max_bytes = max_bytes
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def event(self, kind: str, **fields) -> None:
        record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": kind}
        record.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(record, ensure_ascii=False)
        try:
            self._rotate_if_needed()
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            # Never let logging take the watchdog down; the loop matters more
            # than the record of it.
            pass

    def _rotate_if_needed(self) -> None:
        try:
            if os.path.getsize(self.path) < self.max_bytes:
                return
        except FileNotFoundError:
            return
        os.replace(self.path, f"{self.path}.{KEEP_GENERATIONS}")
