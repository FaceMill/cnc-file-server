"""RS-232 DNC, ported from the serial-dnc project.

One background thread per serial machine: listen on the port, save received
programs, answer "(SEND name)" requests, and drip-feed files with flow control.
The Manager keeps the running workers in step with config/site.json.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime

import serial

from . import config, dnc_files as files, events as db

log = logging.getLogger("dnc")

XON, XOFF = 0x11, 0x13
# NUL leader and DC1-DC4 tape/flow codes are not program content.
STRIP_BYTES = bytes([0x00, 0x11, 0x12, 0x13, 0x14])
CHUNK = 4           # bytes per write. Fanuc alarms (087) if >10 chars arrive after its XOFF
RETRY_SECONDS = 5
MAX_PUNCH = 8 * 1024 * 1024  # bytes; more without a closing % is noise or abuse, not a program
# Changing these reopens the port; anything else (name, delays, skipped types) applies live.
PORT_FIELDS = ("port", "baudrate", "bytesize", "parity", "stopbits", "flow_control")

PARITY = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD,
          "M": serial.PARITY_MARK, "S": serial.PARITY_SPACE}
STOPBITS = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE,
            "2": serial.STOPBITS_TWO}


class Cancelled(Exception):
    pass


class Interrupted(Exception):
    """The machine sent a new punch while a job was running."""


class PauseTimeout(Exception):
    pass


def open_port(m: dict) -> serial.SerialBase:
    kwargs = dict(
        baudrate=int(m["baudrate"]),
        bytesize=int(m["bytesize"]),
        parity=PARITY[m["parity"]],
        stopbits=STOPBITS[m["stopbits"]],
        timeout=0.2,
        # Flow control is done in this program (see _wait_clear_to_send) so a
        # send can always be cancelled and XOFF is honoured on any USB adapter.
        xonxoff=False, rtscts=False, dsrdtr=False,
    )
    if m["port"].startswith("/dev/"):
        kwargs["exclusive"] = True
    return serial.serial_for_url(m["port"], **kwargs)


def _chown(path: str):
    uid, gid = os.environ.get("CNC_UID"), os.environ.get("CNC_GID")
    if uid and gid:
        try:
            os.chown(path, int(uid), int(gid))
        except OSError:
            pass


class MachineWorker(threading.Thread):
    def __init__(self, machine: dict):
        super().__init__(daemon=True, name=f"dnc-{machine['id']}")
        self.m = machine
        self._stop_evt = threading.Event()
        self._cancel = threading.Event()
        self._jobs: queue.Queue = queue.Queue()
        self.ser = None
        self.state = "starting"
        self.detail = ""
        self.current_file = ""
        self.sent = 0
        self.total = 0
        self.send_at = 0.0
        self._last_error = None

    # --- control from the web UI --------------------------------------------

    def queue_send(self, path: str, delay: float):
        self._jobs.put((path, delay, "manual"))

    def cancel(self):
        self._cancel.set()
        # Drop anything queued as well.
        try:
            while True:
                self._jobs.get_nowait()
        except queue.Empty:
            pass

    def stop(self):
        self._stop_evt.set()
        self._cancel.set()

    def status(self) -> dict:
        s = {"state": self.state, "detail": self.detail, "file": self.current_file,
             "sent": self.sent, "total": self.total}
        if self.state == "waiting":
            s["countdown"] = max(0, round(self.send_at - time.time()))
        return s

    # --- thread ---------------------------------------------------------------

    def _event(self, level: str, msg: str):
        getattr(log, "error" if level == "error" else "info")("[%s] %s", self.m["name"], msg)
        db.log_event(self.m, level, msg)

    def run(self):
        while not self._stop_evt.is_set():
            try:
                self.ser = open_port(self.m)
            except Exception as e:
                self.state, self.detail = "error", f"Cannot open {self.m['port']}: {e}"
                if self.detail != self._last_error:
                    self._event("error", self.detail)
                    self._last_error = self.detail
                self._stop_evt.wait(RETRY_SECONDS)
                continue
            self._last_error = None
            self._event("info", f"Port {self.m['port']} opened")
            try:
                self._listen()
            except Exception as e:
                self.state, self.detail = "error", str(e)
                self._event("error", f"Port error: {e}")
            finally:
                try:
                    self.ser.close()
                except Exception:
                    pass
            self._stop_evt.wait(RETRY_SECONDS if not self._stop_evt.is_set() else 0)
        self.state, self.detail = "stopped", ""

    def _idle(self):
        self.state, self.detail, self.current_file = "idle", "Listening", ""
        self.sent = self.total = 0

    def _listen(self):
        buf = bytearray()
        last_rx = 0.0
        self._idle()
        while not self._stop_evt.is_set():
            try:
                path, delay, source = self._jobs.get_nowait()
            except queue.Empty:
                pass
            else:
                interrupted_by = self._send_job(path, delay, source)
                buf.clear()
                self._idle()
                if interrupted_by:
                    self._handle_received(interrupted_by)
                continue

            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                chunk = chunk.translate(None, STRIP_BYTES)
            if chunk:
                buf += chunk
                last_rx = time.time()
                if len(buf) > MAX_PUNCH:
                    self._event("error", f"Discarded {len(buf)} bytes received without an end-of-program %")
                    buf.clear()
                    self._idle()
                    continue
                self.state, self.detail = "receiving", f"{len(buf)} bytes"
                # A complete punch is wrapped in % ... %
                if buf.count(b"%") >= 2:
                    self._handle_received(bytes(buf))
                    buf.clear()
                    self._idle()
            elif buf and time.time() - last_rx >= float(self.m["receive_timeout"]):
                self._handle_received(bytes(buf))
                buf.clear()
                self._idle()

    def _handle_received(self, data: bytes):
        if not data.strip(b"% \r\n\t"):
            return
        name = files.find_request(data)
        if name:
            self._event("info", f"Request received: SEND {name}")
            try:
                path = files.resolve_request(self.m, name)
            except (LookupError, ValueError) as e:
                self._event("error", str(e))
                return
            self._jobs.put((path, float(self.m["send_delay"]), "request"))
            return
        self._save_received(data)

    def _save_received(self, data: bytes):
        try:
            folder = files.resolve_dir(self.m["receive_dir"])
            if not os.path.isdir(folder):
                os.makedirs(folder, exist_ok=True)
                os.chmod(folder, 0o770)
                _chown(folder)
            num = files.program_number(data)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            fname = f"O{num:04d}_{stamp}.nc" if num is not None else f"received_{stamp}.nc"
            path = os.path.join(folder, fname)
            with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o664), "wb") as fh:
                fh.write(data)
            os.chmod(path, 0o664)  # office users edit/delete it through Samba as cncfiles
            _chown(path)
            self._event("info", f"Received program saved as {self.m['receive_dir'] or '/'}"
                                f"/{fname} ({len(data)} bytes)")
        except Exception as e:
            self._event("error", f"Could not save received program: {e}")

    # --- sending --------------------------------------------------------------

    def _send_job(self, path: str, delay: float, source: str) -> bytes | None:
        """Wait the delay, then send the file. Returns the data the machine
        sent if it interrupted the job with a new punch (e.g. after a reset
        and a new call program), so the caller can act on it."""
        self._cancel.clear()
        fname = os.path.basename(path)
        self.current_file = fname
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            self._event("error", f"Cannot read {fname}: {e}")
            return None

        self.ser.reset_input_buffer()
        self._rx = bytearray()
        self._rx_last = 0.0
        self._paused = False
        self.sent, self.total = 0, len(data)
        self.state, self.detail = "waiting", f"Sending {fname} after delay"
        self.send_at = time.time() + delay
        try:
            while time.time() < self.send_at:
                if self._cancel.wait(0.2):
                    raise Cancelled
                self._poll_input()
        except Cancelled:
            self._event("info", f"Send of {fname} cancelled during delay")
            return None
        except Interrupted:
            self._event("info", f"Send of {fname} abandoned during delay: machine sent new data")
            return bytes(self._rx)

        self.state, self.detail = "sending", fname
        self._event("info", f"Sending {fname} ({len(data)} bytes, {source})")
        line_delay = int(self.m["line_delay_ms"]) / 1000.0
        started = time.time()
        try:
            for line in data.splitlines(keepends=True):
                for i in range(0, len(line), CHUNK):
                    piece = line[i:i + CHUNK]
                    self._wait_clear_to_send()
                    self.ser.write(piece)
                    self._drain(len(piece))
                    self.sent += len(piece)
                if line_delay:
                    if self._cancel.wait(line_delay):
                        raise Cancelled
        except Cancelled:
            self._event("info", f"Send of {fname} cancelled at {self.sent}/{self.total} bytes")
            return None
        except PauseTimeout:
            self._event("error", f"Send of {fname} aborted at {self.sent}/{self.total} bytes: "
                                 f"paused longer than {float(self.m['pause_timeout']):g} min")
            return None
        except Interrupted:
            self._event("info", f"Send of {fname} aborted at {self.sent}/{self.total} bytes: "
                                "machine sent new data")
            return bytes(self._rx)
        self._event("info", f"Finished sending {fname} in {time.time() - started:.0f} s")
        return None

    def _drain(self, nbytes: int):
        """Wait roughly until the bytes just written have left the port, so
        little is buffered when the machine sends XOFF. Uses sleep rather than
        tcdrain(), which can block every thread in the process."""
        m = self.m
        bits = 1 + int(m["bytesize"]) + (0 if m["parity"] == "N" else 1) + float(m["stopbits"])
        char_time = bits / int(m["baudrate"])
        time.sleep(nbytes * char_time)
        if sys.platform.startswith("linux"):
            deadline = time.time() + 2
            try:
                while self.ser.out_waiting > 0 and time.time() < deadline:
                    time.sleep(char_time * 4)
            except Exception:
                pass

    def _poll_input(self, block: bool = False):
        """Read what the machine sends during a job. Tracks XON/XOFF, and
        raises Interrupted if the machine punches out a new program/request,
        which means the operator abandoned this run."""
        waiting = self.ser.in_waiting
        if not waiting and not block:
            incoming = b""
        else:
            incoming = self.ser.read(waiting or 1)  # waits up to the port timeout
        if self.m["flow_control"] == "xonxoff":
            for b in incoming:
                if b == XOFF:
                    self._paused = True
                elif b == XON:
                    self._paused = False
        incoming = incoming.translate(None, STRIP_BYTES)
        now = time.time()
        if incoming:
            self._rx += incoming
            self._rx_last = now
            if len(self._rx) > MAX_PUNCH:
                self._rx.clear()
            if self._rx.count(b"%") >= 2:
                raise Interrupted
        elif self._rx and now - self._rx_last >= float(self.m["receive_timeout"]):
            if b"%" in self._rx:
                raise Interrupted
            self._rx.clear()  # stray noise, not a punch

    def _wait_clear_to_send(self):
        fc = self.m["flow_control"]
        timeout = float(self.m["pause_timeout"]) * 60
        blocked_since = None
        while True:
            if self._cancel.is_set() or self._stop_evt.is_set():
                raise Cancelled
            self._poll_input(block=self._paused)
            if fc == "xonxoff":
                clear, why = not self._paused, "paused by XOFF"
            elif fc == "rtscts":
                clear, why = self.ser.cts, "waiting for CTS"
            elif fc == "dsrdtr":
                clear, why = self.ser.dsr, "waiting for DSR"
            else:
                clear, why = True, ""
            if clear:
                break
            now = time.time()
            if blocked_since is None:
                blocked_since = now
            elif timeout and now - blocked_since > timeout:
                raise PauseTimeout
            self.detail = f"{self.current_file} ({why})"
            if not self._paused:
                time.sleep(0.05)  # CTS/DSR wait; the XOFF wait blocks in _poll_input
        self.detail = self.current_file


def worker_config(m: dict) -> dict:
    """serial-dnc's machine settings for a serial machine from site.json."""
    s = m["serial"]
    port = f"socket://{m['ip']}:{s['tcp_port']}" if s["transport"] == "tcp" else s["device"]
    return {
        "id": m["name"], "name": m["label"] or m["name"], "port": port,
        "baudrate": s["baud"], "bytesize": s["data_bits"], "parity": s["parity"],
        "stopbits": s["stop_bits"], "flow_control": s["flow"], "send_delay": s["send_delay"],
        "line_delay_ms": s["line_delay_ms"], "receive_timeout": s["receive_timeout"],
        "pause_timeout": s["pause_timeout"], "excluded_ext": s["excluded_ext"],
        "program_dir": m["name"], "receive_dir": f"{m['name']}/{config.RECEIVED_SUBDIR}",
    }


class Manager:
    def __init__(self):
        self.workers: dict[str, MachineWorker] = {}
        self._lock = threading.Lock()

    def sync(self, site: dict):
        """Start, restart or stop workers so they match the enabled serial machines."""
        wanted = {m["name"]: worker_config(m) for m in config.machines_by_protocol(site, "serial")
                  if m["serial"]["enabled"]}
        with self._lock:
            for mid in list(self.workers):
                cfg, w = wanted.get(mid), self.workers[mid]
                if cfg and all(cfg[k] == w.m[k] for k in PORT_FIELDS):
                    w.m.update(cfg)  # same port settings: apply live, don't interrupt a transfer
                elif cfg != w.m:
                    self._stop(mid)
            for mid, cfg in wanted.items():
                if mid not in self.workers:
                    self._start(cfg)

    def restart(self, machine_id: str):
        with self._lock:
            w = self.workers.get(machine_id)
            if w:
                cfg = w.m
                self._stop(machine_id)
                self._start(cfg)

    def stop_all(self):
        with self._lock:
            for mid in list(self.workers):
                self._stop(mid)

    def _start(self, cfg: dict):
        w = MachineWorker(cfg)
        self.workers[cfg["id"]] = w
        w.start()

    def _stop(self, machine_id: str):
        w = self.workers.pop(machine_id)
        w.stop()
        w.join(timeout=5)

    def get(self, machine_id: str) -> MachineWorker | None:
        return self.workers.get(machine_id)

    def is_busy(self, machine_id: str) -> bool:
        w = self.workers.get(machine_id)
        return bool(w and w.state in ("waiting", "sending", "receiving"))

    def status(self, machine_id: str) -> dict:
        w = self.workers.get(machine_id)
        if not w:
            return {"state": "disabled", "detail": "", "file": "", "sent": 0, "total": 0}
        return w.status()


manager = Manager()
