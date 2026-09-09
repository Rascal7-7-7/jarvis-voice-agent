"""Stand-in for the JARVIS runtime, used to exercise the real restart action.

Publishes a state file the same way the runtime does -- write .tmp, then
os.replace() -- so restart verification is tested against the same atomic
inode-swapping publication, not a simplified one. Then it just sleeps.

Deliberately touches no audio APIs: the point of this test is the launchctl
restart path, not CoreAudio.
"""
from __future__ import annotations

import json
import os
import sys
import time

path = sys.argv[1]
payload = {"state": "IDLE", "since": time.time(),
           "detail": "dummy service", "pid": os.getpid()}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
os.replace(tmp, path)

while True:
    time.sleep(3600)
