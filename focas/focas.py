"""Minimal FANUC FOCAS2 wrapper (ctypes) for program transfer and status.

Uses libfwlib32 for Linux (32-bit ARM, 32-bit x86 or x86-64 build). Structure
layouts follow the FANUC headers as mirrored in Ladder99's fwlib.cs
(github.com/Ladder99/fanuc-driver).
"""
import ctypes
import os
import re
import threading
import time
from contextlib import contextmanager

LIB_PATH = os.environ.get("FWLIB_PATH", "/usr/local/lib/libfwlib32.so")
# FANUC's "long" is 32 bits in every build, including x86-64 (where C's long is 64):
# fwlib.cs passes it as a C# int on all platforms.
FLONG = ctypes.c_int32

EW_OK, EW_BUFFER = 0, 10
ERRORS = {
    -17: "protocol error", -16: "cannot connect (check IP, cable, and that FOCAS/Ethernet is enabled on port 8193)",
    -15: "FOCAS library missing", -8: "invalid connection handle", -2: "CNC was reset or stopped during the transfer",
    -1: "CNC is busy, try again", 1: "function not supported by this CNC", 2: "invalid data length",
    3: "program number not found or invalid", 4: "invalid data attribute", 5: "data error (program exists, bad format, or memory full)",
    6: "CNC option not available", 7: "write protected (memory protect key or parameter)", 8: "CNC memory overflow",
    9: "invalid parameter", 10: "buffer full", 11: "CNC password protected", 12: "CNC mode error (e.g. must be in EDIT)",
    13: "rejected: the CNC is running or the program is in use", 15: "CNC alarm",
}

AUT_MODES = {0: "MDI", 1: "MEM", 2: "----", 3: "EDIT", 4: "HANDLE", 5: "JOG", 6: "TEACH JOG",
             7: "TEACH HANDLE", 8: "INC FEED", 9: "REF", 10: "REMOTE"}
RUN_STATES = {0: "RESET", 1: "STOP", 2: "HOLD", 3: "START", 4: "MSTR"}
EMERGENCY = {0: "", 1: "EMERGENCY STOP", 2: "RESET", 3: "WAIT"}


class ODBST(ctypes.Structure):  # cnc_statinfo (0i/16i/18i/21i/30i layout)
    _fields_ = [("dummy", ctypes.c_short), ("tmmode", ctypes.c_short), ("aut", ctypes.c_short),
                ("run", ctypes.c_short), ("motion", ctypes.c_short), ("mstb", ctypes.c_short),
                ("emergency", ctypes.c_short), ("alarm", ctypes.c_short), ("edit", ctypes.c_short)]


class DIR3DATE(ctypes.Structure):
    _fields_ = [(n, ctypes.c_short) for n in ("year", "month", "day", "hour", "minute", "dummy")]


class PRGDIR3(ctypes.Structure):  # one entry of cnc_rdprogdir3, 88 bytes
    _fields_ = [("number", ctypes.c_int), ("length", ctypes.c_int), ("page", ctypes.c_int),
                ("comment", ctypes.c_char * 52), ("mdate", DIR3DATE), ("cdate", DIR3DATE)]


class ODBEXEPRG(ctypes.Structure):  # cnc_exeprgname
    _fields_ = [("name", ctypes.c_char * 36), ("o_num", ctypes.c_int)]


class ODBERR(ctypes.Structure):  # cnc_getdtailerr
    _fields_ = [("err_no", ctypes.c_short), ("err_dtno", ctypes.c_short)]


assert ctypes.sizeof(PRGDIR3) == 88 and ctypes.sizeof(ODBST) == 18 and ctypes.sizeof(ODBEXEPRG) == 40


class FocasError(Exception):
    def __init__(self, func, code, detail=None):
        self.code = code
        msg = f"{func}: {ERRORS.get(code, 'error')} (code {code}"
        msg += f", detail {detail})" if detail else ")"
        super().__init__(msg)


TRANSFER_SECONDS = 120  # give up on a CNC that stays busy


_lib = None
_lib_lock = threading.Lock()


def lib():
    """Load libfwlib32 once; the Linux library needs cnc_startupprocess first."""
    global _lib
    with _lib_lock:
        if _lib is None:
            l = ctypes.CDLL(LIB_PATH)
            s, us, p = ctypes.c_short, ctypes.c_ushort, ctypes.POINTER
            l.cnc_startupprocess.argtypes, l.cnc_startupprocess.restype = [FLONG, ctypes.c_char_p], s
            l.cnc_allclibhndl3.argtypes, l.cnc_allclibhndl3.restype = [ctypes.c_char_p, us, FLONG, p(us)], s
            l.cnc_freelibhndl.argtypes, l.cnc_freelibhndl.restype = [us], s
            l.cnc_statinfo.argtypes, l.cnc_statinfo.restype = [us, p(ODBST)], s
            l.cnc_exeprgname.argtypes, l.cnc_exeprgname.restype = [us, p(ODBEXEPRG)], s
            l.cnc_rdprogdir3.argtypes, l.cnc_rdprogdir3.restype = [us, s, p(FLONG), p(s), p(PRGDIR3)], s
            l.cnc_dwnstart3.argtypes, l.cnc_dwnstart3.restype = [us, s], s
            l.cnc_download3.argtypes, l.cnc_download3.restype = [us, p(FLONG), ctypes.c_char_p], s
            l.cnc_dwnend3.argtypes, l.cnc_dwnend3.restype = [us], s
            l.cnc_upstart3.argtypes, l.cnc_upstart3.restype = [us, s, FLONG, FLONG], s
            l.cnc_upload3.argtypes, l.cnc_upload3.restype = [us, p(FLONG), ctypes.c_char_p], s
            l.cnc_upend3.argtypes, l.cnc_upend3.restype = [us], s
            l.cnc_delete.argtypes, l.cnc_delete.restype = [us, s], s
            l.cnc_getdtailerr.argtypes, l.cnc_getdtailerr.restype = [us, p(ODBERR)], s
            ret = l.cnc_startupprocess(0, b"/tmp/focas.log")
            if ret != EW_OK:
                raise FocasError("cnc_startupprocess", ret)
            _lib = l
        return _lib


@contextmanager
def connect(ip, port=8193, timeout=10):
    l = lib()
    h = ctypes.c_ushort()
    ret = l.cnc_allclibhndl3(ip.encode(), port, timeout, ctypes.byref(h))
    if ret != EW_OK:
        raise FocasError("connect", ret)
    try:
        yield h.value
    finally:
        l.cnc_freelibhndl(h.value)


def _check(func, h, ret):
    if ret != EW_OK:
        detail = None
        if ret in (5, 13):
            err = ODBERR()
            if lib().cnc_getdtailerr(h, ctypes.byref(err)) == EW_OK:
                detail = f"{err.err_no}/{err.err_dtno}"
        raise FocasError(func, ret, detail)


def status(h):
    st, exe = ODBST(), ODBEXEPRG()
    _check("cnc_statinfo", h, lib().cnc_statinfo(h, ctypes.byref(st)))
    running = None
    if lib().cnc_exeprgname(h, ctypes.byref(exe)) == EW_OK:
        running = {"name": exe.name.split(b"\0")[0].decode("latin-1"), "number": exe.o_num}
    return {"mode": AUT_MODES.get(st.aut, str(st.aut)), "run": RUN_STATES.get(st.run, str(st.run)),
            "emergency": EMERGENCY.get(st.emergency, str(st.emergency)), "alarm": bool(st.alarm),
            "running": running}


def directory(h):
    """All programs in CNC memory: number, length, comment, modified date."""
    out, top = [], FLONG(0)
    buf = (PRGDIR3 * 10)()
    for _ in range(10000):  # at most 100000 programs; never loop forever
        num = ctypes.c_short(10)
        ret = lib().cnc_rdprogdir3(h, 2, ctypes.byref(top), ctypes.byref(num), buf)
        if ret != EW_OK and out and ret in (3, 5):
            return out  # past the last program (some series report this as an error)
        _check("cnc_rdprogdir3", h, ret)
        for d in buf[:num.value]:
            md = d.mdate
            out.append({"number": d.number, "length": d.length,
                        "comment": d.comment.split(b"\0")[0].decode("latin-1").strip(),
                        "modified": f"{md.year:04d}-{md.month:02d}-{md.day:02d} {md.hour:02d}:{md.minute:02d}"
                        if md.year else ""})
        if num.value < 10:
            return out
        top = FLONG(buf[num.value - 1].number + 1)
    return out


PROGRAM_NO_RE = re.compile(rb"^\s*O(\d{1,4})\b", re.M)


def to_cnc_format(data: bytes) -> tuple[bytes, int]:
    """FOCAS download format: LF line endings, starts with LF + O-number, ends with %."""
    text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").strip()
    text = text.strip(b"%").strip()
    m = PROGRAM_NO_RE.search(text)
    if not m or not text.startswith(b"O"):
        raise ValueError("The program must start with an O-number (O1-O9999), e.g. O1234")
    return b"\n" + text + b"\n%", int(m.group(1))


def download(h, data: bytes):
    """Write a program into CNC memory (cnc_dwnstart3/download3/dwnend3)."""
    l = lib()
    _check("cnc_dwnstart3", h, l.cnc_dwnstart3(h, 0))
    try:
        pos = 0
        deadline = time.time() + TRANSFER_SECONDS
        while pos < len(data):
            if time.time() > deadline:
                raise TimeoutError(f"download did not finish within {TRANSFER_SECONDS} s (CNC busy)")
            chunk = data[pos:pos + 1280]
            n = FLONG(len(chunk))
            ret = l.cnc_download3(h, ctypes.byref(n), chunk)
            if ret == EW_BUFFER:
                time.sleep(0.02)
                continue
            _check("cnc_download3", h, ret)
            pos += n.value
    except Exception:
        l.cnc_dwnend3(h)
        raise
    _check("cnc_dwnend3", h, l.cnc_dwnend3(h))


def upload(h, number: int) -> bytes:
    """Read one program out of CNC memory (cnc_upstart3/upload3/upend3)."""
    if not 1 <= number <= 9999:
        raise ValueError("program number must be 1-9999")
    l = lib()
    _check("cnc_upstart3", h, l.cnc_upstart3(h, 0, number, number))
    out = bytearray()
    try:
        buf = ctypes.create_string_buffer(1281)
        deadline = time.time() + TRANSFER_SECONDS
        while time.time() < deadline:
            n = FLONG(1280)
            ret = l.cnc_upload3(h, ctypes.byref(n), buf)
            if ret == EW_BUFFER:
                time.sleep(0.02)
                continue
            _check("cnc_upload3", h, ret)
            out += buf.raw[:n.value]
            # The program ends at the first % after its content (a leading % is just the start mark).
            if b"%" in bytes(out).lstrip(b"\0\r\n %"):
                break
        else:
            raise TimeoutError(f"upload did not finish within {TRANSFER_SECONDS} s")
    finally:
        l.cnc_upend3(h)
    return bytes(out)


def delete(h, number: int):
    if not 1 <= number <= 9999:
        raise ValueError("program number must be 1-9999")
    _check("cnc_delete", h, lib().cnc_delete(h, number))
