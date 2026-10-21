import copy
import datetime
import glob
import itertools
import functools
import hmac
import ipaddress
import os
import re
import secrets
import threading
import time

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from . import config, system
from .config import MACHINE_TYPES, PROTOCOLS, SERIAL_CHOICES, SERIAL_DEFAULTS
from . import appearance, dnc_files, events, focas_client, monitor, recycle
from .serial_dnc import manager as dnc
from .network import CONFIRM_SECONDS, office_change
from .ntlm import nt_hash

bp = Blueprint("web", __name__)

# POSTs still allowed while an office network change awaits confirmation.
PENDING_ALLOWED = {"web.login", "web.logout", "web.network_confirm", "web.network_revert",
                   "web.dnc_send", "web.dnc_cancel", "web.dnc_restart",
                   "web.focas_send", "web.focas_read", "web.focas_delete",
                   "web.file_upload", "web.file_delete"}


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("web.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@bp.before_app_request
def csrf_and_pending_guard():
    if request.method != "POST":
        return None
    token = session.get("csrf")
    if not token or not hmac.compare_digest(token, request.form.get("csrf", "")):
        abort(400, "Form expired, reload the page and try again.")
    if office_change.pending and request.endpoint not in PENDING_ALLOWED:
        flash("Confirm or revert the pending office network change first.", "error")
        return redirect(url_for("web.network"))
    return None


@bp.app_context_processor
def inject_globals():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    look = config.load()["appearance"] if config.exists() else appearance.normalize({})
    return {"csrf": session["csrf"], "pending": office_change.status(),
            "theme_vars": appearance.css_vars(look), "look": look,
            "pending_error": office_change.last_error, "machine_types": MACHINE_TYPES,
            "protocols": PROTOCOLS, "protocol_of": config.protocol}


def client_ip():
    # Only Caddy on localhost talks to us, so its X-Forwarded-For is trustworthy.
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def apply(site, success):
    """Validate, save, and apply a modified site config. Returns True on success."""
    try:
        config.save(site)
    except ValueError as e:
        flash(str(e), "error")
        return False
    try:
        actions = system.apply_site(site)
    except Exception as e:
        flash(f"Saved, but applying failed: {e}", "error")
        return False
    flash(success + (f" ({', '.join(actions)})" if actions else ""), "ok")
    return True


# --- auth -------------------------------------------------------------------

LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 300
_login_failures = {}  # client IP -> [timestamps of recent failures]
_login_lock = threading.Lock()


def _recent_failures(ip):
    now = time.time()
    with _login_lock:
        times = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_LOCKOUT_SECONDS]
        _login_failures[ip] = times
        return times


def safe_next(nxt):
    """Only local paths: '/x' but not '//host' or '/\\host' (browsers treat \\ as /)."""
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and ":" not in nxt.split("?")[0]:
        return nxt
    return url_for("web.dashboard")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = client_ip()
        if len(_recent_failures(ip)) >= LOGIN_MAX_FAILURES:
            flash(f"Too many failed logins from {ip}. Try again in {LOGIN_LOCKOUT_SECONDS // 60} minutes.", "error")
            return render_template("login.html", configured=True), 429
        stored = config.admin_password_hash()
        if (request.form.get("username") == "admin" and stored
                and check_password_hash(stored, request.form.get("password", ""))):
            session.clear()
            session["user"] = "admin"
            return redirect(safe_next(request.args.get("next", "")))
        with _login_lock:
            _login_failures.setdefault(ip, []).append(time.time())
        flash("Wrong username or password.", "error")
    return render_template("login.html", configured=config.admin_password_hash() is not None)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.login"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        current, new, repeat = (request.form.get(k, "") for k in ("current", "new", "repeat"))
        if not check_password_hash(config.admin_password_hash(), current):
            flash("Current password is wrong.", "error")
        elif len(new) < 10:
            flash("New password must be at least 10 characters.", "error")
        elif new != repeat:
            flash("New passwords do not match.", "error")
        else:
            config.set_admin_password_hash(generate_password_hash(new))
            flash("Admin password changed.", "ok")
            return redirect(url_for("web.dashboard"))
    return render_template("account.html")


# --- dashboard --------------------------------------------------------------

@bp.get("/")
@login_required
def dashboard():
    site = config.load()
    serial_status = {m["name"]: dnc.status(m["name"]) for m in config.machines_by_protocol(site, "serial")}
    recent = events.recent_events(15)
    adapters = {m["name"]: os.path.exists(m["serial"]["device"])
                for m in config.machines_by_protocol(site, "serial") if m["serial"]["transport"] == "usb"}
    return render_template(
        "dashboard.html", site=site,
        reachable=system.ping_many([m["ip"] for m in site["machines"] if m["ip"]]),
        connected=system.smb_sessions() | system.ftp_sessions(),
        serial_status=serial_status, adapters=adapters, events=recent,
        health=monitor.issues(), host=monitor.host_snapshot(), backup=monitor.backup_status(),
        containers=system.container_states(),
        ifaces=system.interfaces(),
        disk=system.disk_usage(),
    )


# --- machines ---------------------------------------------------------------

def _form_number(key, default, kind=int):
    try:
        return kind(request.form.get(key, default))
    except ValueError:
        return None  # rejected by validation with a clear message


def machine_from_form(existing=None):
    m = dict(existing or {})
    if existing is None:
        m["name"] = request.form.get("name", "").strip().lower()
    m["label"] = request.form.get("label", "").strip() or m["name"]
    m["type"] = request.form.get("type", "")
    m["ip"] = request.form.get("ip", "").strip()
    password = request.form.get("password", "").strip()
    m["password"] = password or m.get("password") or config.gen_password()

    if m["type"] in MACHINE_TYPES and config.protocol(m) == "serial":
        s = dict(SERIAL_DEFAULTS)
        for key in ("transport", "parity", "stop_bits", "flow"):
            s[key] = request.form.get(f"serial_{key}", s[key])
        for key in ("tcp_port", "baud", "data_bits", "line_delay_ms"):
            s[key] = _form_number(f"serial_{key}", s[key])
        for key in ("send_delay", "receive_timeout", "pause_timeout"):
            s[key] = _form_number(f"serial_{key}", s[key], float)
        s["enabled"] = bool(request.form.get("serial_enabled"))
        s["excluded_ext"] = ", ".join(sorted(dnc_files.parse_excluded(request.form.get("serial_excluded_ext", ""))))
        s["device"] = (request.form.get("serial_device_manual", "").strip()
                       or request.form.get("serial_device", "").strip())
        if s["transport"] == "usb":
            m["ip"] = ""
        m["serial"] = s
    else:
        m.pop("serial", None)

    if m["type"] in MACHINE_TYPES and config.protocol(m) == "focas":
        m["focas"] = {"port": _form_number("focas_port", 8193), "timeout": _form_number("focas_timeout", 10),
                      "replace": bool(request.form.get("focas_replace"))}
    else:
        m.pop("focas", None)
    return m


def available_ports():
    """Serial ports the container can see, stable names first (from serial-dnc)."""
    ports = sorted(glob.glob("/dev/cnc-*"))                 # custom udev names (docs/SERIAL-PORTS.md)
    ports += sorted(glob.glob("/dev/serial/by-id/*"))
    ports += sorted(glob.glob("/dev/serial/by-path/*"))
    ports += sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    return ports


def form_context(site, m, new):
    return {"site": site, "m": m, "new": new, "ports": available_ports(),
            "serial": {**SERIAL_DEFAULTS, **m.get("serial", {})}, "serial_choices": SERIAL_CHOICES,
            "focas": config.normalize_focas(m.get("focas"))}


@bp.get("/machines")
@login_required
def machines():
    return render_template("machines.html", site=config.load())


@bp.route("/machines/new", methods=["GET", "POST"])
@login_required
def machine_new():
    with config.lock:
        site = config.load()
        if request.method == "POST":
            m = machine_from_form()
            site["machines"].append(m)
            if apply(site, f"Machine '{m['label']}' added"):
                return redirect(url_for("web.machine_detail", name=m["name"]))
            return render_template("machine_form.html", **form_context(site, m, True))
    net = config.cnc_network(site)
    used = {m["ip"] for m in site["machines"]} | {config.cnc_server_ip(site)}
    next_ip = next((str(h) for h in itertools.islice(net.hosts(), 10, 10000) if str(h) not in used), "")
    return render_template("machine_form.html", **form_context(
        site, {"name": "", "label": "", "type": "haas-classic", "ip": next_ip, "password": ""}, True))


@bp.get("/machines/<name>")
@login_required
def machine_detail(name):
    site = config.load()
    m = config.find_machine(site, name) or abort(404)
    return render_template("machine_detail.html", site=site, m=m, proto=config.protocol(m),
                           server_ip=config.cnc_server_ip(site),
                           netmask=str(config.cnc_network(site).netmask),
                           files=system.machine_files(name), **serial_context(m))


def serial_context(m):
    if config.protocol(m) == "focas":
        received = sorted(dnc_files.list_files(f"{m['name']}/{config.RECEIVED_SUBDIR}"),
                          key=lambda f: -f["mtime"])[:50]
        return {"programs": dnc_files.list_files(m["name"]), "received": received,
                "events": events.recent_events(50, m["name"])}
    if config.protocol(m) != "serial":
        return {}
    excluded = dnc_files.parse_excluded(m["serial"]["excluded_ext"])
    received = sorted(dnc_files.list_files(f"{m['name']}/{config.RECEIVED_SUBDIR}"),
                      key=lambda f: -f["mtime"])[:50]
    return {"programs": dnc_files.list_files(m["name"], excluded), "received": received,
            "dnc_status": dnc.status(m["name"]), "events": events.recent_events(50, m["name"]),
            "running": dnc.get(m["name"]) is not None}


@bp.route("/machines/<name>/edit", methods=["GET", "POST"])
@login_required
def machine_edit(name):
    with config.lock:
        site = config.load()
        m = config.find_machine(site, name) or abort(404)
        if request.method == "POST" and dnc.is_busy(name):
            flash("A serial transfer is running for this machine; wait for it or cancel it first.", "error")
        elif request.method == "POST":
            m.update(machine_from_form(existing=m))
            if apply(site, f"Machine '{m['label']}' updated"):
                return redirect(url_for("web.machine_detail", name=name))
    return render_template("machine_form.html", **form_context(site, m, False))


@bp.post("/machines/<name>/delete")
@login_required
def machine_delete(name):
    with config.lock:
        site = config.load()
        m = config.find_machine(site, name) or abort(404)
        if dnc.is_busy(name):
            flash("A serial transfer is running for this machine; cancel it first.", "error")
            return redirect(url_for("web.machine_detail", name=name))
        site["machines"].remove(m)
        if apply(site, f"Machine '{m['label']}' removed"):
            dest = system.archive_machine_dir(name)
            if dest:
                flash(f"Its files were kept in {dest}.", "ok")
    return redirect(url_for("web.machines"))


# --- serial DNC and file viewing -----------------------------------------

VIEW_LIMIT = 1_000_000  # bytes shown in the browser; larger files are truncated


def serial_machine(name):
    m = config.find_machine(config.load(), name)
    if not m or config.protocol(m) != "serial":
        abort(404)
    return m


def machine_file(m, folder, filename):
    """Absolute path of a file in a machine's folder or its received/ subfolder."""
    if folder == "received":
        path = dnc_files.safe_file(f"{m['name']}/{config.RECEIVED_SUBDIR}", filename)
    elif folder == "programs":
        if config.protocol(m) == "serial" and dnc_files.is_excluded(
                filename, dnc_files.parse_excluded(m["serial"]["excluded_ext"])):
            raise ValueError("File type is skipped for this machine")
        path = dnc_files.safe_file(m["name"], filename)
    else:
        raise ValueError("Invalid folder")
    if not os.path.isfile(path):
        raise ValueError("File not found")
    return path


@bp.post("/machines/<name>/dnc/send")
@login_required
def dnc_send(name):
    m = serial_machine(name)
    w = dnc.get(name)
    filename = request.form.get("filename", "")
    if not w:
        flash("The machine's port is not running (disabled in its settings?).", "error")
        return redirect(url_for("web.machine_detail", name=name))
    if w.state in ("error", "starting", "stopped"):
        flash(f"The serial port isn't open ({w.detail or w.state}); nothing was queued.", "error")
        return redirect(url_for("web.machine_detail", name=name))
    try:
        path = machine_file(m, "programs", filename)
        delay = max(0.0, float(request.form.get("delay", m["serial"]["send_delay"]) or 0))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("web.machine_detail", name=name))
    events.log_event(w.m, "info", f"Manual send of {filename} queued from the web page")
    w.queue_send(path, delay)
    flash(f"{filename} queued; sending in {delay:g} s.", "ok")
    return redirect(url_for("web.machine_detail", name=name))


@bp.post("/machines/<name>/dnc/cancel")
@login_required
def dnc_cancel(name):
    serial_machine(name)
    w = dnc.get(name)
    if w:
        w.cancel()
    flash("Cancel requested.", "ok")
    return redirect(url_for("web.machine_detail", name=name))


@bp.post("/machines/<name>/dnc/restart")
@login_required
def dnc_restart(name):
    serial_machine(name)
    dnc.restart(name)
    flash("Port restarted.", "ok")
    return redirect(url_for("web.machine_detail", name=name))


@bp.get("/machines/<name>/dnc/status")
@login_required
def dnc_status(name):
    serial_machine(name)
    return jsonify(dnc.status(name))


# --- FOCAS -------------------------------------------------------------------

def focas_machine(name):
    m = config.find_machine(config.load(), name)
    if not m or config.protocol(m) != "focas":
        abort(404)
    return m


def focas_log(m, level, message):
    events.log_event({"id": m["name"], "name": m["label"] or m["name"]}, level, message)


@bp.get("/machines/<name>/focas/status")
@login_required
def focas_status(name):
    m = focas_machine(name)
    try:
        listing = focas_client.programs(m, request.args.get("folder") or None)
        return jsonify({"status": focas_client.status(m), **listing})
    except (ValueError, focas_client.FocasError) as e:
        return jsonify({"error": str(e)})


@bp.post("/machines/<name>/focas/send")
@login_required
def focas_send(name):
    m = focas_machine(name)
    filename = request.form.get("filename", "")
    replace = bool(request.form.get("replace"))
    folder = request.form.get("folder") or None
    try:
        with open(machine_file(m, "programs", filename), "rb") as f:
            data = f.read()
        if replace:
            _backup_before_replace(m, data, folder)
        res = focas_client.download(m, data, replace, folder)
    except (ValueError, focas_client.FocasError) as e:
        focas_log(m, "error", f"Send of {filename} to CNC failed: {e}")
        flash(f"Send failed: {e}", "error")
    else:
        what = "replaced" if res["replaced"] else "written"
        label = res.get("name") or f"O{res['number']}"
        where = f" in {folder}" if folder else ""
        focas_log(m, "info", f"{filename} {what} as {label}{where} in CNC memory ({res['bytes']} bytes)")
        flash(f"{filename} {what} as {label}{where} in CNC memory.", "ok")
    return redirect(url_for("web.machine_detail", name=name))


def _cnc_number(raw):
    n = int(raw)
    if not 1 <= n <= 9999:
        raise ValueError("Program number must be 1-9999.")
    return n


def _cnc_program():
    """(number, name) from the form: a full path for CNCs with program folders, else an O-number."""
    if request.form.get("name"):
        return None, focas_client.program_path(request.form["name"])
    return _cnc_number(request.form.get("number", "")), None


def _program_label(number, name):
    return name if name else f"O{number}"


def _received_name(number, name, suffix=""):
    """received/ file name for a program read from the CNC: O0042_<time>.nc or PART-A_<time>.nc."""
    base = name.rsplit("/", 1)[1] if name else f"O{number:04d}"
    base = re.sub(r"[^A-Za-z0-9_+-]", "_", base)
    return f"{base}_{datetime.datetime.now():%Y%m%d-%H%M%S}{suffix}.nc"


def _backup_before_replace(m, data, folder=None):
    """Save the CNC's current copy to received/ before it is replaced (kept even if the
    bridge's own restore fails)."""
    key = focas_client.program_key(data)
    if key is None:
        return
    number = name = None
    try:
        if folder:
            listing = focas_client.programs(m, folder)
            entry = next((e for e in listing.get("entries", [])
                          if not e["folder"] and focas_client.same_program(e["name"], key)), None)
            if not entry:
                return  # not on the CNC yet: nothing to keep
            name = folder + entry["name"]
        elif isinstance(key, int) and 1 <= key <= 9999:
            number = key
        else:
            return
        old = focas_client.upload(m, number=number, name=name)
    except (ValueError, focas_client.FocasError):
        return  # not on the CNC yet: nothing to keep
    folder_dir = dnc_files.resolve_dir(f"{m['name']}/{config.RECEIVED_SUBDIR}")
    for n in range(100):  # two replaces within a second must not lose (or crash on) a copy
        fname = _received_name(number, name, "_before-replace" + (f"-{n}" if n else ""))
        try:
            system.write_shared_file(os.path.join(folder_dir, fname), old)
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("Could not save a copy of the CNC's program before replacing it; nothing was changed.")
    focas_log(m, "info", f"{_program_label(number, name)}: copy from the CNC saved as received/{fname} before replacing it")


@bp.post("/machines/<name>/focas/read")
@login_required
def focas_read(name):
    m = focas_machine(name)
    try:
        number, path = _cnc_program()
        data = focas_client.upload(m, number=number, name=path)
    except (ValueError, focas_client.FocasError) as e:
        focas_log(m, "error", f"Read from CNC failed: {e}")
        flash(f"Read failed: {e}", "error")
        return redirect(url_for("web.machine_detail", name=name))
    folder = dnc_files.resolve_dir(f"{m['name']}/{config.RECEIVED_SUBDIR}")
    fname = _received_name(number, path)
    try:
        system.write_shared_file(os.path.join(folder, fname), data)
    except FileExistsError:
        flash("A copy was saved less than a second ago; try again.", "error")
        return redirect(url_for("web.machine_detail", name=name))
    label = _program_label(number, path)
    focas_log(m, "info", f"{label} read from CNC memory, saved as received/{fname} ({len(data)} bytes)")
    flash(f"{label} saved as received/{fname}.", "ok")
    return redirect(url_for("web.machine_detail", name=name))


@bp.post("/machines/<name>/focas/delete")
@login_required
def focas_delete(name):
    m = focas_machine(name)
    try:
        number, path = _cnc_program()
        focas_client.delete(m, number=number, name=path)
    except (ValueError, focas_client.FocasError) as e:
        focas_log(m, "error", f"Delete from CNC failed: {e}")
        flash(f"Delete failed: {e}", "error")
    else:
        label = _program_label(number, path)
        focas_log(m, "info", f"{label} deleted from CNC memory")
        flash(f"{label} deleted from CNC memory.", "ok")
    return redirect(url_for("web.machine_detail", name=name))


@bp.get("/machines/<name>/view")
@login_required
def file_view(name):
    m = config.find_machine(config.load(), name) or abort(404)
    folder, filename = request.args.get("folder", "programs"), request.args.get("file", "")
    try:
        path = machine_file(m, folder, filename)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("web.machine_detail", name=name))
    st = os.stat(path)
    with open(path, "rb") as fh:
        data = fh.read(VIEW_LIMIT)
    return render_template("view.html", m=m, folder=folder, filename=filename, size=st.st_size,
                           mtime=datetime.datetime.fromtimestamp(st.st_mtime),
                           lines=data.decode("latin-1").splitlines(), truncated=st.st_size > VIEW_LIMIT,
                           can_send=folder == "programs" and dnc.get(name) is not None)


@bp.get("/machines/<name>/download")
@login_required
def file_download(name):
    m = config.find_machine(config.load(), name) or abort(404)
    try:
        path = machine_file(m, request.args.get("folder", "programs"), request.args.get("file", ""))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("web.machine_detail", name=name))
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/octet-stream")


# --- upload / delete (replaces File Browser) --------------------------------

@bp.post("/machines/<name>/upload")
@login_required
def file_upload(name):
    m = config.find_machine(config.load(), name) or abort(404)
    f = request.files.get("file")
    filename = secure_filename(f.filename) if f and f.filename else ""
    if not filename or filename.startswith("."):
        flash("Choose a file to upload.", "error")
        return redirect(url_for("web.machine_detail", name=name))
    if filename.lower() in (config.RECEIVED_SUBDIR, "recycle"):
        flash(f"'{filename}' is a reserved name; rename the file first.", "error")
        return redirect(url_for("web.machine_detail", name=name))
    try:
        path = dnc_files.safe_file(m["name"], filename)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("web.machine_detail", name=name))
    data = f.read()
    if os.path.lexists(path):
        if not request.form.get("overwrite"):
            flash(f"{filename} already exists; tick 'replace' to overwrite it.", "error")
            return redirect(url_for("web.machine_detail", name=name))
        _move_to_recycle(m, path)
    try:
        system.write_shared_file(path, data)
    except FileExistsError:
        flash(f"{filename} was just written by someone else (a machine or the office); upload again to replace it.", "error")
        return redirect(url_for("web.machine_detail", name=name))
    events.log_event({"id": m["name"], "name": m["label"] or m["name"]}, "info",
                     f"{filename} uploaded from the web page ({len(data)} bytes)")
    flash(f"{filename} uploaded ({len(data):,} bytes).", "ok")
    return redirect(url_for("web.machine_detail", name=name))


@bp.post("/machines/<name>/delete-file")
@login_required
def file_delete(name):
    m = config.find_machine(config.load(), name) or abort(404)
    folder, filename = request.form.get("folder", "programs"), request.form.get("file", "")
    try:
        path = machine_file(m, folder, filename)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("web.machine_detail", name=name))
    _move_to_recycle(m, path)
    events.log_event({"id": m["name"], "name": m["label"] or m["name"]}, "info",
                     f"{filename} moved to the recycle folder from the web page")
    flash(f"{filename} moved to {m['name']}/.recycle/.", "ok")
    return redirect(url_for("web.machine_detail", name=name))


def _move_to_recycle(m, path):
    """Like Samba's recycle bin: keep deleted or overwritten files in <machine>/.recycle/."""
    recycle = os.path.join(dnc_files.resolve_dir(m["name"]), ".recycle")
    system.make_shared_dir(recycle)
    base = os.path.basename(path)
    dest = os.path.join(recycle, base)
    n = 0
    while os.path.lexists(dest):  # never overwrite an earlier recycled copy
        n += 1
        dest = os.path.join(recycle, f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{n}-{base}")
    os.rename(path, dest)


# --- settings ---------------------------------------------------------------

@bp.get("/settings")
@login_required
def settings():
    return render_template("settings.html", site=config.load(), recycle_stats=recycle.stats(),
                           swatches=appearance.swatches())


@bp.post("/settings/appearance")
@login_required
def settings_appearance():
    with config.lock:
        site = config.load()
        a = site["appearance"]
        a["theme"] = request.form.get("theme", "default")
        a["accent"] = request.form.get("accent", "").strip() if request.form.get("use_accent") else ""
        try:
            config.save(site)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("web.settings"))
    flash("Colours saved.", "ok")
    return redirect(url_for("web.settings"))


@bp.post("/settings/logo")
@login_required
def settings_logo():
    f = request.files.get("logo")
    with config.lock:
        site = config.load()
        try:
            ext = appearance.save_logo(f.read(appearance.LOGO_MAX_BYTES + 1) if f else b"")
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("web.settings"))
        site["appearance"].update(logo=ext, logo_version=int(time.time()))
        config.save(site)
    flash("Logo uploaded.", "ok")
    return redirect(url_for("web.settings"))


@bp.post("/settings/logo/remove")
@login_required
def settings_logo_remove():
    with config.lock:
        site = config.load()
        appearance.remove_logo()
        site["appearance"].update(logo="", logo_version=0)
        config.save(site)
    flash("Logo removed.", "ok")
    return redirect(url_for("web.settings"))


@bp.get("/branding/logo")
def branding_logo():
    """Public (the login page shows it). Only ever a PNG/JPEG checked on upload."""
    ext = config.load()["appearance"]["logo"] if config.exists() else ""
    if not ext or not os.path.isfile(appearance.logo_path(ext)):
        abort(404)
    resp = send_file(appearance.logo_path(ext), mimetype=appearance.LOGO_TYPES[ext], max_age=86400)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    return resp


@bp.post("/settings/alerts")
@login_required
def settings_alerts():
    with config.lock:
        site = config.load()
        a = site["alerts"]
        f = request.form
        a.update(enabled=bool(f.get("enabled")), smtp_host=f.get("smtp_host", "").strip(),
                 security=f.get("security", "starttls"), username=f.get("username", "").strip(),
                 sender=f.get("sender", "").strip(),
                 recipients=[r for r in f.get("recipients", "").replace(",", " ").replace(";", " ").split() if r])
        a["smtp_port"] = _form_number("smtp_port", 25)
        if f.get("password"):
            a["password"] = f["password"]
        if f.get("clear_password"):
            a["password"] = ""
        try:
            config.save(site)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("web.settings"))
    flash("Alert settings saved." + ("" if a["enabled"] else " Alerts are off."), "ok")
    return redirect(url_for("web.settings"))


@bp.post("/settings/alerts/test")
@login_required
def settings_alerts_test():
    a = config.load()["alerts"]
    if not (a["smtp_host"] and a["sender"] and a["recipients"]):
        flash("Fill in and save the SMTP server, sender and recipients first.", "error")
        return redirect(url_for("web.settings"))
    current = monitor.issues()
    body = ("This is a test message from the CNC file server.\n\n"
            + ("Current problems:\n" + "\n".join(f"  [{i['level'].upper()}] {i['message']}" for i in current)
               if current else "No problems right now."))
    try:
        monitor.send_email(a, f"[{monitor.server_name()}] test alert", body)
    except Exception as e:
        flash(f"Sending failed: {e}", "error")
    else:
        flash(f"Test email sent to {', '.join(a['recipients'])}.", "ok")
    return redirect(url_for("web.settings"))


@bp.post("/settings/recycle")
@login_required
def settings_recycle():
    with config.lock:
        site = config.load()
        try:
            site["recycle"]["retention_days"] = int(request.form.get("retention_days", ""))
            config.save(site)
        except ValueError as e:
            msg = str(e) if "Recycle" in str(e) else "Enter a whole number of days (0 = never)."
            flash(msg, "error")
            return redirect(url_for("web.settings"))
    days = site["recycle"]["retention_days"]
    flash(f"Recycle folders are now emptied of files older than {days} days." if days
          else "Automatic emptying of the recycle folders is off.", "ok")
    return redirect(url_for("web.settings"))


@bp.post("/settings/recycle/empty")
@login_required
def settings_recycle_empty():
    removed, freed = recycle.purge()
    events.log_event(None, "info", f"Recycle folders emptied from the web page: {removed} file(s), {freed / 1e6:.1f} MB")
    flash(f"Recycle folders emptied: {removed} file(s), {freed / 1e6:.1f} MB freed.", "ok")
    return redirect(url_for("web.settings"))


# --- office users -----------------------------------------------------------

def password_from_form():
    pw, repeat = request.form.get("password", ""), request.form.get("repeat", "")
    if len(pw) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if pw != repeat:
        raise ValueError("Passwords do not match.")
    return nt_hash(pw)


@bp.get("/users")
@login_required
def users():
    return render_template("users.html", site=config.load())


@bp.post("/users/add")
@login_required
def user_add():
    with config.lock:
        site = config.load()
        try:
            site["office_users"].append({"name": request.form.get("name", "").strip().lower(),
                                         "nt_hash": password_from_form()})
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("web.users"))
        apply(site, "User added")
    return redirect(url_for("web.users"))


@bp.post("/users/<name>/password")
@login_required
def user_password(name):
    with config.lock:
        site = config.load()
        u = config.find_office_user(site, name) or abort(404)
        try:
            u["nt_hash"] = password_from_form()
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("web.users"))
        apply(site, f"Password for '{name}' changed")
    return redirect(url_for("web.users"))


@bp.post("/users/<name>/delete")
@login_required
def user_delete(name):
    with config.lock:
        site = config.load()
        site["office_users"].remove(config.find_office_user(site, name) or abort(404))
        apply(site, f"User '{name}' removed")
    return redirect(url_for("web.users"))


# --- network ----------------------------------------------------------------

@bp.get("/network")
@login_required
def network():
    return render_template("network.html", site=config.load(), ifaces=system.interfaces(),
                           client_ip=client_ip(), confirm_seconds=CONFIRM_SECONDS)


@bp.post("/network/general")
@login_required
def network_general():
    with config.lock:
        site = config.load()
        site["workgroup"] = request.form.get("workgroup", "").strip().upper()
        apply(site, "Workgroup updated")
    return redirect(url_for("web.network"))


@bp.post("/network/cnc")
@login_required
def network_cnc():
    with config.lock:
        site = config.load()
        old_address = site["cnc"]["address"]
        site["cnc"]["address"] = request.form.get("address", "").strip()
        for m in site["machines"]:
            m["ip"] = request.form.get(f"ip_{m['name']}", m["ip"]).strip()
        try:
            config.validate(site)
            if site["cnc"]["address"] != old_address:
                try:
                    system.nm_set_cnc(site["cnc"]["address"])
                except Exception as e:
                    # "modify" may have succeeded before "up" failed: put the old address back.
                    try:
                        system.nm_set_cnc(old_address)
                        back = "the previous address was restored"
                    except Exception as e2:
                        back = f"RESTORING the previous address also failed ({e2}); check the Pi's network"
                    flash(f"Not saved: changing the CNC interface address failed ({e}); {back}.", "error")
                    return redirect(url_for("web.network"))
            config.save(site)
            actions = system.apply_site(site)
            flash("CNC network updated" + (f" ({', '.join(actions)})" if actions else ""), "ok")
            flash("Remember to change the IP settings on the machines to match.", "ok")
        except ValueError as e:
            flash(str(e), "error")
        except Exception as e:
            flash(f"Saved, but applying failed: {e}", "error")
    return redirect(url_for("web.network"))


@bp.post("/network/office")
@login_required
def network_office():
    site = config.load()
    if site["office"].get("hotspot"):
        flash("The office side is the Pi's setup hotspot; its address is fixed. To use a real office "
              "network, set OFFICE_MODE=client in .env and run the installer again.", "error")
        return redirect(url_for("web.network"))
    new = copy.deepcopy(site["office"])
    new["method"] = request.form.get("method", "auto")
    new["address"] = request.form.get("address", "").strip() if new["method"] == "manual" else ""
    new["gateway"] = request.form.get("gateway", "").strip() if new["method"] == "manual" else ""
    new["dns"] = request.form.get("dns", "").replace(",", " ").split() if new["method"] == "manual" else []
    new["allowed_subnets"] = request.form.get("allowed_subnets", "").replace(",", " ").split()

    try:
        ip = ipaddress.ip_address(client_ip())
        nets = [ipaddress.ip_network(s, strict=False) for s in new["allowed_subnets"]]
        if not any(ip in n for n in nets) and not request.form.get("lockout_ok"):
            flash(f"Your computer ({ip}) is not in the new allowed subnets and would be locked out. "
                  "Tick the override box if that is intended.", "error")
            return redirect(url_for("web.network"))
        office_change.start(new)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("web.network"))

    if new["method"] == "manual" and new["address"] != site["office"]["address"]:
        new_ip = new["address"].split("/")[0]
        flash(f"Applying. Open https://{new_ip}:8443/network, log in, and click Confirm "
              f"within {CONFIRM_SECONDS // 60} minutes, or the old settings come back.", "ok")
    else:
        flash(f"Applying. Reload this page and click Confirm within {CONFIRM_SECONDS // 60} minutes, "
              "or the old settings come back.", "ok")
    return redirect(url_for("web.network"))


@bp.post("/network/confirm")
@login_required
def network_confirm():
    if office_change.confirm():
        flash("Office network change confirmed.", "ok")
    else:
        flash("Nothing to confirm: the change is still being applied, or it was already reverted.", "error")
    return redirect(url_for("web.network"))


@bp.post("/network/revert")
@login_required
def network_revert():
    office_change.revert()
    flash("Office network change reverted.", "ok")
    return redirect(url_for("web.network"))


# --- logs -------------------------------------------------------------------

@bp.get("/logs")
@login_required
def logs():
    name = request.args.get("c", "dnc")
    if name == "dnc":
        return render_template("logs.html", name=name, services=system.SERVICES,
                               events=events.recent_events(1000))
    if name not in system.SERVICES:
        abort(404)
    return render_template("logs.html", name=name, services=system.SERVICES,
                           text=system.container_logs(name))
