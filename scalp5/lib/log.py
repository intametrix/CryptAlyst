"""log - structured, levelled logging. Design pattern: Adapter over print."""
import json, os, sys, threading, time

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
_lock = threading.Lock()
_LEVEL = LEVELS[os.environ.get("LOG_LEVEL", "info")]


def _emit(level, msg, **fields):
    if LEVELS[level] < _LEVEL:
        return
    rec = {"ts": time.strftime("%H:%M:%S"), "level": level, "msg": msg}
    if fields:
        rec.update(fields)
    line = "%s %-5s %s" % (rec["ts"], level.upper(), msg)
    if fields:
        line += " " + json.dumps(fields, default=str)
    with _lock:
        print(line, file=sys.stderr if level in ("warn", "error") else sys.stdout, flush=True)


def debug(msg, **f): _emit("debug", msg, **f)
def info(msg, **f): _emit("info", msg, **f)
def warn(msg, **f): _emit("warn", msg, **f)
def error(msg, **f): _emit("error", msg, **f)
