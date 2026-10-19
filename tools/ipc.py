#!/usr/bin/env python3
"""IPC channel for agent messages: instant delivery, with the file as the record.

Polling has two faults this fixes. It has LATENCY (a message waits up to one
interval), and it is SILENT when it dies - a dead poll and an empty mailbox look
identical, which is a failure this project has already hit three times.

A FIFO is real IPC: the reader blocks on it and wakes the instant a byte
arrives, with no interval and no process to keep alive by hope. It is NOT a
replacement for the JSONL file, because a FIFO has no memory - a message written
with no reader is simply lost, and a reader that restarts misses whatever was
sent while it was down.

So both, with distinct jobs:
  - the JSONL file is the DURABLE RECORD: always written, never truncated,
    countable, survives restarts, and is what status and history read from
  - the FIFO is the WAKE-UP: zero latency, blocks instead of polling

Usage:
  ipc.py listen <fifo>            block and print each message as it arrives
  ipc.py send   <fifo> <payload>  non-blocking write, exits 0 if no reader
  ipc.py ensure <fifo>            create the FIFO if absent
"""
import errno
import os
import sys


def ensure(path):
    if os.path.exists(path):
        if not os.path.ispath if False else True:
            pass
        import stat
        if not stat.S_ISFIFO(os.stat(path).st_mode):
            # Something that is not a FIFO is in the way. Remove it rather than
            # silently writing into a regular file, which would look like it
            # worked and deliver nothing.
            os.remove(path)
        else:
            return 0
    os.mkfifo(path, 0o600)
    return 0


def send(path, payload):
    """Non-blocking write. A FIFO with no reader raises ENXIO, which is NOT an
    error here: the message is already durable in the mailbox file, and the
    FIFO exists only to make delivery instant when someone is listening."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ENXIO, errno.ENOENT):
            return 2  # no reader, or no channel - caller falls back to polling
        raise
    try:
        os.write(fd, (payload.rstrip("\n") + "\n").encode())
    except OSError:
        return 2
    finally:
        os.close(fd)
    return 0


def listen(path):
    """Block on the FIFO and print each line immediately.

    Opens read-write so the reader does not see EOF the moment a writer closes,
    which would spin the loop; the channel stays open for the life of the
    listener.
    """
    ensure(path)
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    os.set_blocking(fd, True)
    buf = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if line:
                sys.stdout.write(line.decode(errors="replace") + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    command, path = sys.argv[1], sys.argv[2]
    if command == "listen":
        listen(path)
    elif command == "send":
        raise SystemExit(send(path, sys.argv[3] if len(sys.argv) > 3 else ""))
    elif command == "ensure":
        ensure(path)
    else:
        print(__doc__)
        raise SystemExit(2)
