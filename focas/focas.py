"""Minimal FANUC FOCAS2 wrapper (ctypes) for program transfer and status.

Uses libfwlib32 for Linux (32-bit ARM, 32-bit x86 or x86-64 build). Structure
layouts and prototypes follow FANUC's fwlib32.h (also mirrored in Ladder99's
fwlib.cs, github.com/Ladder99/fanuc-driver).

Two ways to address programs:
- by O-number (O1-O9999): cnc_rdprogdir3, cnc_upstart3, cnc_dwnstart3, cnc_delete.
  Every FOCAS control.
- by folder and name ("//CNC_MEM/USER/PATH1/O1234"): cnc_rdpdf_*, cnc_upstart4,
  cnc_dwnstart4, cnc_pdf_del. Controls with program folders (30i/31i/32i, 0i-F);
  allows 8-digit O-numbers and named programs.
"""
import ctypes
import os
import re
import threading
import time
from contextlib import contextmanager

LIB_PATH = os.environ.get("FWLIB_PATH", "/usr/local/lib/libfwlib32.so")
# fwlib32.h declares these values as C "long", but FANUC's x86-64 build treats them as
# 32 bits: its internal functions take int* (e.g. inter_rdprogdir(Handle&, short, int*,
# short*, prgdir3*)) and cnc_download4 reads the length with a 32-bit load. On the
# 32-bit builds long is 32 bits anyway. So never use c_long (64 bits on x86-64 Linux).
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
    _fields_ = [("number", FLONG), ("length", FLONG), ("page", FLONG),
                ("comment", ctypes.c_char * 52), ("mdate", DIR3DATE), ("cdate", DIR3DATE)]


class ODBEXEPRG(ctypes.Structure):  # cnc_exeprgname
    _fields_ = [("name", ctypes.c_char * 36), ("o_num", FLONG)]


class ODBERR(ctypes.Structure):  # cnc_getdtailerr
    _fields_ = [("err_no", ctypes.c_short), ("err_dtno", ctypes.c_short)]


class ODBSYS(ctypes.Structure):  # cnc_sysinfo
    _fields_ = [("addinfo", ctypes.c_short), ("max_axis", ctypes.c_short), ("cnc_type", ctypes.c_char * 2),
                ("mt_type", ctypes.c_char * 2), ("series", ctypes.c_char * 4), ("version", ctypes.c_char * 4),
                ("axes", ctypes.c_char * 2)]


class ODBALMMSG2(ctypes.Structure):  # cnc_rdalmmsg2
    _fields_ = [("alm_no", FLONG), ("type", ctypes.c_short), ("axis", ctypes.c_short), ("dummy", ctypes.c_short),
                ("msg_len", ctypes.c_short), ("alm_msg", ctypes.c_char * 64)]


class OPMSG(ctypes.Structure):  # cnc_rdopmsg (all series except 15/15i)
    _fields_ = [("datano", ctypes.c_short), ("type", ctypes.c_short), ("char_num", ctypes.c_short),
                ("data", ctypes.c_char * 256)]


class ODBPDFNFIL(ctypes.Structure):  # cnc_rdpdf_subdirn
    _fields_ = [("dir_num", ctypes.c_short), ("file_num", ctypes.c_short)]


class IDBPDFADIR(ctypes.Structure):  # cnc_rdpdf_alldir request
    _fields_ = [("path", ctypes.c_char * 212), ("req_num", ctypes.c_short), ("size_kind", ctypes.c_short),
                ("type", ctypes.c_short), ("dummy", ctypes.c_short)]


class ODBPDFADIR(ctypes.Structure):  # cnc_rdpdf_alldir entry
    _fields_ = [("data_kind", ctypes.c_short), ("year", ctypes.c_short), ("mon", ctypes.c_short),
                ("day", ctypes.c_short), ("hour", ctypes.c_short), ("min", ctypes.c_short), ("sec", ctypes.c_short),
                ("dummy", ctypes.c_short), ("dummy2", FLONG), ("size", FLONG), ("attr", ctypes.c_uint32),
                ("d_f", ctypes.c_char * 36), ("comment", ctypes.c_char * 52), ("o_time", ctypes.c_char * 12)]


assert ctypes.sizeof(PRGDIR3) == 88 and ctypes.sizeof(ODBST) == 18 and ctypes.sizeof(ODBEXEPRG) == 40
assert ctypes.sizeof(ODBSYS) == 18 and ctypes.sizeof(ODBALMMSG2) == 76 and ctypes.sizeof(OPMSG) == 262
assert ctypes.sizeof(IDBPDFADIR) == 220 and ctypes.sizeof(ODBPDFADIR) == 128


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
            s, us, p, cp = ctypes.c_short, ctypes.c_ushort, ctypes.POINTER, ctypes.c_char_p
            l.cnc_startupprocess.argtypes, l.cnc_startupprocess.restype = [FLONG, cp], s
            l.cnc_allclibhndl3.argtypes, l.cnc_allclibhndl3.restype = [cp, us, FLONG, p(us)], s
            l.cnc_freelibhndl.argtypes, l.cnc_freelibhndl.restype = [us], s
            l.cnc_statinfo.argtypes, l.cnc_statinfo.restype = [us, p(ODBST)], s
            l.cnc_exeprgname.argtypes, l.cnc_exeprgname.restype = [us, p(ODBEXEPRG)], s
            l.cnc_sysinfo.argtypes, l.cnc_sysinfo.restype = [us, p(ODBSYS)], s
            l.cnc_rdalmmsg2.argtypes, l.cnc_rdalmmsg2.restype = [us, s, p(s), p(ODBALMMSG2)], s
            l.cnc_rdopmsg.argtypes, l.cnc_rdopmsg.restype = [us, s, s, p(OPMSG)], s
            l.cnc_rdprogdir3.argtypes, l.cnc_rdprogdir3.restype = [us, s, p(FLONG), p(s), p(PRGDIR3)], s
            l.cnc_dwnstart3.argtypes, l.cnc_dwnstart3.restype = [us, s], s
            l.cnc_download3.argtypes, l.cnc_download3.restype = [us, p(FLONG), cp], s
            l.cnc_dwnend3.argtypes, l.cnc_dwnend3.restype = [us], s
            l.cnc_upstart3.argtypes, l.cnc_upstart3.restype = [us, s, FLONG, FLONG], s
            l.cnc_upload3.argtypes, l.cnc_upload3.restype = [us, p(FLONG), cp], s
            l.cnc_upend3.argtypes, l.cnc_upend3.restype = [us], s
            l.cnc_delete.argtypes, l.cnc_delete.restype = [us, s], s
            l.cnc_rdpdf_curdir.argtypes, l.cnc_rdpdf_curdir.restype = [us, s, cp], s
            l.cnc_rdpdf_subdirn.argtypes, l.cnc_rdpdf_subdirn.restype = [us, cp, p(ODBPDFNFIL)], s
            l.cnc_rdpdf_alldir.argtypes, l.cnc_rdpdf_alldir.restype = [us, p(s), p(IDBPDFADIR), p(ODBPDFADIR)], s
            l.cnc_dwnstart4.argtypes, l.cnc_dwnstart4.restype = [us, s, cp], s
            l.cnc_download4.argtypes, l.cnc_download4.restype = [us, p(FLONG), cp], s
            l.cnc_dwnend4.argtypes, l.cnc_dwnend4.restype = [us], s
            l.cnc_upstart4.argtypes, l.cnc_upstart4.restype = [us, s, cp], s
            l.cnc_upload4.argtypes, l.cnc_upload4.restype = [us, p(FLONG), cp], s
            l.cnc_upend4.argtypes, l.cnc_upend4.restype = [us], s
            l.cnc_pdf_del.argtypes, l.cnc_pdf_del.restype = [us, cp], s
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


def _text(raw: bytes) -> str:
    return raw.split(b"\0")[0].decode("latin-1").strip()


def _optional(fn, *args):
    """Extra details: a CNC that doesn't support one still reports its status."""
    try:
        return fn(*args)
    except FocasError:
        return None


def status(h, details=False):
    """Mode, run state, alarm flag and active program. With details: also the control
    model, the active alarms' text and the operator message."""
    st, exe = ODBST(), ODBEXEPRG()
    _check("cnc_statinfo", h, lib().cnc_statinfo(h, ctypes.byref(st)))
    running = None
    if lib().cnc_exeprgname(h, ctypes.byref(exe)) == EW_OK:
        running = {"name": _text(exe.name), "number": exe.o_num}
    out = {"mode": AUT_MODES.get(st.aut, str(st.aut)), "run": RUN_STATES.get(st.run, str(st.run)),
           "emergency": EMERGENCY.get(st.emergency, str(st.emergency)), "alarm": bool(st.alarm),
           "running": running}
    if details:
        out["cnc"] = _optional(sysinfo, h)
        out["alarms"] = (_optional(alarms, h) or []) if st.alarm else []
        out["message"] = _optional(operator_message, h)
    return out


def sysinfo(h):
    """Control model, e.g. {"model": "0i-M", "series": "D4F1", "version": "30.0", "axes": "3"}."""
    s = ODBSYS()
    _check("cnc_sysinfo", h, lib().cnc_sysinfo(h, ctypes.byref(s)))
    cnc, mt = _text(s.cnc_type), _text(s.mt_type)
    model = cnc + ("i" if s.addinfo & 0x2 else "") + (f"-{mt}" if mt else "")  # addinfo bit 1: i Series
    return {"model": model, "series": _text(s.series), "version": _text(s.version),
            "axes": _text(s.axes).lstrip("0") or "0"}


def alarms(h, limit=10):
    """Active alarms with their text (all types)."""
    buf = (ODBALMMSG2 * limit)()
    num = ctypes.c_short(limit)
    _check("cnc_rdalmmsg2", h, lib().cnc_rdalmmsg2(h, -1, ctypes.byref(num), buf))
    out = []
    for a in buf[:max(0, min(num.value, limit))]:
        n = max(0, min(a.msg_len, 64))
        out.append({"number": a.alm_no, "type": a.type, "axis": a.axis,
                    "message": bytes(a.alm_msg)[:n].decode("latin-1").strip()})
    return out


def operator_message(h):
    """The first operator message (#3006 / external message), or None."""
    m = OPMSG()
    _check("cnc_rdopmsg", h, lib().cnc_rdopmsg(h, 0, ctypes.sizeof(OPMSG), ctypes.byref(m)))
    if m.datano == -1:
        return None
    n = max(0, min(m.char_num, 256))
    return bytes(m.data)[:n].decode("latin-1").strip() or None


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
                        "comment": _text(d.comment),
                        "modified": f"{md.year:04d}-{md.month:02d}-{md.day:02d} {md.hour:02d}:{md.minute:02d}"
                        if md.year else ""})
        if num.value < 10:
            return out
        top = FLONG(buf[num.value - 1].number + 1)
    return out


# --- program folders (30i/31i/32i, 0i-F) ------------------------------------------

def current_folder(h):
    """The foreground program folder, e.g. "//CNC_MEM/USER/PATH1/", or None if this
    CNC has no program folders (it answers with a positive FOCAS error code)."""
    buf = ctypes.create_string_buffer(256)
    ret = lib().cnc_rdpdf_curdir(h, 1, buf)
    if ret > 0:
        return None
    _check("cnc_rdpdf_curdir", h, ret)
    return buf.value.decode("latin-1")


def folder(h, path, limit=2000):
    """Sub-folders and programs in a folder: name, folder (bool), size, comment, modified."""
    l = lib()
    count = ODBPDFNFIL()
    _check("cnc_rdpdf_subdirn", h, l.cnc_rdpdf_subdirn(h, path.encode(), ctypes.byref(count)))
    total = min(limit, max(0, count.dir_num) + max(0, count.file_num))
    out = []
    buf = (ODBPDFADIR * 10)()
    while len(out) < total:
        req = IDBPDFADIR(path=path.encode(), req_num=len(out), size_kind=1, type=1)  # sizes in bytes, with details
        num = ctypes.c_short(min(10, total - len(out)))
        _check("cnc_rdpdf_alldir", h, l.cnc_rdpdf_alldir(h, ctypes.byref(num), ctypes.byref(req), buf))
        if num.value <= 0:
            break
        for e in buf[:min(num.value, 10)]:
            out.append({"name": _text(e.d_f), "folder": e.data_kind == 0, "size": e.size,
                        "comment": _text(e.comment),
                        "modified": f"{e.year:04d}-{e.mon:02d}-{e.day:02d} {e.hour:02d}:{e.min:02d}" if e.year else ""})
    return out


# --- program text --------------------------------------------------------------

PROGRAM_NO_RE = re.compile(rb"^\s*O(\d{1,4})\b", re.M)
HEADER_RE = re.compile(rb"^(?:O(\d{1,8})\b|<([A-Za-z0-9_.+-]{1,32})>)")


def to_cnc_format(data: bytes, names=False):
    """FOCAS download format: LF line endings, starts with LF + program header, ends with %.

    Returns (data, key): key is the O-number (int), or with names=True (program folders)
    the O-number up to 8 digits or the name from a <NAME> header (str)."""
    text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").strip()
    text = text.strip(b"%").strip()
    if not names:
        m = PROGRAM_NO_RE.search(text)
        if not m or not text.startswith(b"O"):
            raise ValueError("The program must start with an O-number (O1-O9999), e.g. O1234")
        return b"\n" + text + b"\n%", int(m.group(1))
    m = HEADER_RE.match(text)
    if not m:
        raise ValueError("The program must start with an O-number (O1-O99999999) or a <NAME> line")
    return b"\n" + text + b"\n%", int(m.group(1)) if m.group(1) else m.group(2).decode()


def same_program(name: str, key) -> bool:
    """Does a program file name on the CNC ("O0042", "PART-A") match a key from to_cnc_format?"""
    if isinstance(key, int):
        m = re.fullmatch(r"O0*(\d{1,8})", name)
        return bool(m) and int(m.group(1)) == key
    return name == key


def program_label(key) -> str:
    return f"O{key}" if isinstance(key, int) else key


# --- transfers -----------------------------------------------------------------

def _write(h, start, chunk_fn, end, data: bytes):
    _check(*start())
    try:
        pos = 0
        deadline = time.time() + TRANSFER_SECONDS
        while pos < len(data):
            if time.time() > deadline:
                raise TimeoutError(f"download did not finish within {TRANSFER_SECONDS} s (CNC busy)")
            chunk = data[pos:pos + 1280]
            n = FLONG(len(chunk))
            ret = chunk_fn(h, ctypes.byref(n), chunk)
            if ret == EW_BUFFER:
                time.sleep(0.02)
                continue
            _check(chunk_fn.__name__, h, ret)
            pos += n.value
    except Exception:
        end(h)
        raise
    _check(end.__name__, h, end(h))


def _read(h, start, chunk_fn, end) -> bytes:
    _check(*start())
    out = bytearray()
    try:
        buf = ctypes.create_string_buffer(1281)
        deadline = time.time() + TRANSFER_SECONDS
        while time.time() < deadline:
            n = FLONG(1280)
            ret = chunk_fn(h, ctypes.byref(n), buf)
            if ret == EW_BUFFER:
                time.sleep(0.02)
                continue
            _check(chunk_fn.__name__, h, ret)
            out += buf.raw[:max(0, min(n.value, 1280))]
            # The program ends at the first % after its content (a leading % is just the start mark).
            if b"%" in bytes(out).lstrip(b"\0\r\n %"):
                break
        else:
            raise TimeoutError(f"upload did not finish within {TRANSFER_SECONDS} s")
    finally:
        end(h)
    return bytes(out)


def download(h, data: bytes):
    """Write a program into CNC memory by O-number (cnc_dwnstart3/download3/dwnend3)."""
    l = lib()
    _write(h, lambda: ("cnc_dwnstart3", h, l.cnc_dwnstart3(h, 0)), l.cnc_download3, l.cnc_dwnend3, data)


def download_to(h, path: str, data: bytes):
    """Write a program into a program folder (cnc_dwnstart4/download4/dwnend4)."""
    l = lib()
    _write(h, lambda: ("cnc_dwnstart4", h, l.cnc_dwnstart4(h, 0, path.encode())), l.cnc_download4, l.cnc_dwnend4, data)


def upload(h, number: int) -> bytes:
    """Read one program out of CNC memory by O-number (cnc_upstart3/upload3/upend3)."""
    if not 1 <= number <= 9999:
        raise ValueError("program number must be 1-9999")
    l = lib()
    return _read(h, lambda: ("cnc_upstart3", h, l.cnc_upstart3(h, 0, number, number)), l.cnc_upload3, l.cnc_upend3)


def upload_name(h, path: str) -> bytes:
    """Read one program by its full path (cnc_upstart4/upload4/upend4)."""
    l = lib()
    return _read(h, lambda: ("cnc_upstart4", h, l.cnc_upstart4(h, 0, path.encode())), l.cnc_upload4, l.cnc_upend4)


def delete(h, number: int):
    if not 1 <= number <= 9999:
        raise ValueError("program number must be 1-9999")
    _check("cnc_delete", h, lib().cnc_delete(h, number))


def delete_name(h, path: str):
    _check("cnc_pdf_del", h, lib().cnc_pdf_del(h, path.encode()))
