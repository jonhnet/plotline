#!/usr/bin/env python3
"""Thin serial bridge: exclusive port ownership, byte I/O, and flow control.

Protocol 2 carries generic read/write packets over one persistent SSH session.
There are no jobs, HPGL commands, paper checks, plot state, or files to replay.
Python 3.7+; standard library only.
"""
import argparse
import array
import fcntl
import json
import os
from pathlib import Path
import select
import socket
import socketserver
import sys
import termios
import threading
import time

PROTOCOL = 2
MAX_FRAME = 16384
MAX_WRITE = 512
TIMING = "tx_queue_timing_v1"


class Timing:
    """Observe host-queue byte boundaries, never infer UART/plotter receipts."""
    def __init__(self, serial, emit):
        self.origin = time.monotonic_ns()
        self.base = serial.written
        self.emit = emit
        self.samples = []
        self.previous_poll = 0
        self.previous_counts = None
        self.last_flush = self.origin
        self.metadata = {
            "schema": TIMING, "clock": "time.monotonic_ns", "origin_ns": self.origin,
            "wall_time_ns": time.time_ns(), "baud": 9600, "bits_per_byte": 10,
            "drain_poll_interval_ms": 20, "write_wait_interval_ms": 50,
            "ack_queue_threshold_bytes": 64,
            "columns": ["elapsed_ns", "previous_poll_ns", "accepted_bytes", "kernel_pending_bytes"],
            "meaning": "Host transmit queue only; USB adapter and plotter buffers are unobserved",
        }

    def sample(self, serial, force=False):
        pending = serial.pending()
        now = time.monotonic_ns()
        elapsed = now - self.origin
        counts = (serial.written - self.base, pending)
        if force or counts != self.previous_counts:
            self.samples.append([elapsed, self.previous_poll, *counts])
            self.previous_counts = counts
        # Even unchanged polls narrow the interval of the next observed change.
        self.previous_poll = elapsed
        if len(self.samples) >= 64 or now - self.last_flush >= 1_000_000_000:
            self.flush()
        return pending

    def flush(self):
        if self.samples:
            self.emit({"event": TIMING, "samples": self.samples})
            self.samples = []
        self.last_flush = time.monotonic_ns()


def receive(connection):
    # One byte here would be expensive on ARMv6; MSG_PEEK is unnecessary for
    # framing because each session has a single buffered reader in Handler.
    return connection.recv(65536)


class Serial:
    def __init__(self, device, timeout=120):
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.timeout = timeout
        self.written = 0
        try:
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.CRTSCTS
            attrs[4] = attrs[5] = termios.B9600
            attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check_connection(connection):
        if select.select([connection], [], [], 0)[0] and not connection.recv(1, socket.MSG_PEEK):
            raise ConnectionError("Serial connection closed")

    def pending(self):
        queued = array.array("i", [0])
        fcntl.ioctl(self.fd, termios.TIOCOUTQ, queued, True)
        return queued[0]

    def write(self, data, connection, timing=None):
        deadline = time.monotonic() + self.timeout
        offset = 0
        if timing:
            timing.sample(self, force=True)
        while True:
            self.check_connection(connection)
            if time.monotonic() >= deadline:
                raise TimeoutError("Serial flow control stalled")
            if offset < len(data):
                if timing:
                    timing.sample(self)
                if select.select([], [self.fd], [], .05)[1]:
                    try:
                        count = os.write(self.fd, data[offset:])
                    except BlockingIOError:
                        continue
                    if count <= 0:
                        raise OSError("Serial write made no progress")
                    offset += count
                    self.written += count
                    if timing:
                        timing.sample(self)
            else:
                queued = timing.sample(self) if timing else self.pending()
                if queued <= 64:
                    if timing:
                        timing.sample(self, force=True)
                        timing.flush()
                    return len(data)
                time.sleep(.02)

    def read(self, size, timeout_ms):
        if not select.select([self.fd], [], [], timeout_ms / 1000)[0]:
            return b""
        data = os.read(self.fd, size)
        if not data:
            raise OSError("Serial adapter disconnected")
        return data

    def close(self, abort=False):
        if self.fd is not None:
            try:
                if abort:
                    termios.tcflush(self.fd, termios.TCOFLUSH)
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
            finally:
                os.close(self.fd)
                self.fd = None


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path, device, serial_factory=Serial):
        self.device = device
        self.serial_factory = serial_factory
        self.port_lock = threading.Lock()
        super().__init__(path, Handler)

    def status(self):
        return {"protocol": PROTOCOL, "capabilities": ["serial_bridge", TIMING],
                "serial_present": Path(self.device).exists(), "device": self.device,
                "busy": self.port_lock.locked()}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        serial = None
        owned = False
        clean = False
        timing = None
        try:
            while True:
                line = self.rfile.readline(MAX_FRAME + 1)
                if not line:
                    break
                if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                    raise ValueError("Invalid transport frame")
                request = json.loads(line)
                if request.get("protocol") != PROTOCOL:
                    raise ValueError("Use serial bridge protocol 2")
                op = request.get("op")
                if op == "status":
                    if timing:
                        timing.sample(serial, force=True)
                        timing.flush()
                    result = self.server.status()
                elif op == "open" and serial is None:
                    owned = self.server.port_lock.acquire(False)
                    if not owned:
                        raise RuntimeError("Serial port is already in use")
                    serial = self.server.serial_factory(self.server.device)
                    result = {"opened": True, "protocol": PROTOCOL, "capabilities": [TIMING]}
                elif op == "timing" and serial is not None and timing is None:
                    timing = Timing(serial, self.reply)
                    # The caller records the epoch before any sample frames.
                    self.reply({"ok": True, "result": timing.metadata})
                    timing.sample(serial, force=True)
                    timing.flush()
                    continue
                elif op == "write" and serial is not None:
                    values = request.get("data")
                    if not isinstance(values, list) or not 1 <= len(values) <= MAX_WRITE:
                        raise ValueError("Write must contain 1..512 bytes")
                    if any(type(n) is not int or not 0 <= n <= 255 for n in values):
                        raise ValueError("Invalid byte")
                    result = {"written": serial.write(bytes(values), self.request, timing)}
                elif op == "read" and serial is not None:
                    size, timeout = request.get("size", 1024), request.get("timeout_ms", 0)
                    if type(size) is not int or not 1 <= size <= 1024 or type(timeout) is not int or not 0 <= timeout <= 5000:
                        raise ValueError("Invalid read size or timeout")
                    result = {"data": list(serial.read(size, timeout))}
                elif op == "close":
                    if serial is not None:
                        if timing:
                            timing.sample(serial, force=True)
                            timing.flush()
                        serial.close()
                        serial = None
                    clean = True
                    self.reply({"ok": True, "result": {"closed": True}})
                    return
                else:
                    raise ValueError("Open the serial port before byte I/O; unknown operation")
                self.reply({"ok": True, "result": result})
        except Exception as error:
            try:
                if timing:
                    timing.flush()
                self.reply({"ok": False, "error": str(error)})
            except OSError:
                pass
        finally:
            if serial is not None:
                try:
                    serial.close(abort=not clean)
                except OSError:
                    pass
            if owned:
                self.server.port_lock.release()

    def reply(self, value):
        self.wfile.write((json.dumps(value) + "\n").encode())
        self.wfile.flush()


def client(path):
    # This process owns no plotter policy. It simply carries the persistent
    # framed byte connection between SSH stdio and the local bridge socket.
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(path)
        readers = [sys.stdin.buffer, connection]
        while True:
            ready, _, _ = select.select(readers, [], [])
            if sys.stdin.buffer in ready:
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    connection.shutdown(socket.SHUT_WR)
                    readers.remove(sys.stdin.buffer)
                else:
                    connection.sendall(data)
            if connection in ready:
                data = receive(connection)
                if not data:
                    return
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["serve", "client"])
    # Existing paired Pis retain their account and filesystem identity.
    namespace = "squidprint" if Path("/etc/squidprint/device.json").exists() and not Path("/etc/plotline/device.json").exists() else "plotline"
    parser.add_argument("--socket", default=f"/run/{namespace}/device.sock")
    parser.add_argument("--config", default=f"/etc/{namespace}/device.json")
    args = parser.parse_args()
    if args.mode == "client":
        client(args.socket)
        return
    os.umask(0o007)
    path = Path(args.socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path.parent / "daemon.lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists():
            path.unlink()
        config = json.loads(Path(args.config).read_text())
        with Server(str(path), config["device"]) as server:
            os.chmod(str(path), 0o660)
            server.serve_forever()


if __name__ == "__main__":
    main()
