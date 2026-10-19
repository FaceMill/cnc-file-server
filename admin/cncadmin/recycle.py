"""Recycle folders: where deleted files go, and emptying them.

Samba (office share and machine shares) and the admin page move deleted files into
.recycle folders instead of deleting them:
    /srv/cnc/.recycle/...            office share (keeps the folder tree)
    /srv/cnc/<machine>/.recycle/...  machine shares and the admin page

Moving a file there is a rename, which sets the file's change time (ctime) to the
moment it was deleted; nothing else touches the file while it sits there. So
"in the recycle folder for N days" is "ctime older than N days".
"""
import logging
import os
import threading
import time

from . import config, events, system

log = logging.getLogger("cncadmin")

CHECK_EVERY_SECONDS = 6 * 3600   # age-based, so running often is harmless
FIRST_CHECK_SECONDS = 300        # not right at boot
DAY = 86400


def recycle_dirs():
    """Every .recycle folder directly in /srv/cnc or in a machine folder (never followed through symlinks)."""
    root = system.SRV_DIR
    candidates = [os.path.join(root, ".recycle")]
    try:
        candidates += [os.path.join(e.path, ".recycle") for e in os.scandir(root)
                       if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")]
    except FileNotFoundError:
        return []
    return [d for d in candidates if os.path.isdir(d) and not os.path.islink(d)]


def _walk_files(top):
    """(path, lstat) of every file or symlink under top, without following symlinks."""
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            path = os.path.join(dirpath, name)
            try:
                yield path, os.lstat(path)
            except FileNotFoundError:
                continue


def stats():
    """{"files", "bytes", "oldest_days"} across all recycle folders."""
    files = size = 0
    oldest = None
    now = time.time()
    for d in recycle_dirs():
        for _, st in _walk_files(d):
            files += 1
            size += st.st_size
            age = (now - st.st_ctime) / DAY
            oldest = age if oldest is None else max(oldest, age)
    return {"files": files, "bytes": size, "oldest_days": oldest}


def purge(older_than_days=None, now=None):
    """Permanently delete recycled files older than the given number of days
    (None = everything). Returns (files, bytes) removed."""
    now = time.time() if now is None else now
    cutoff = None if older_than_days is None else now - older_than_days * DAY
    removed = freed = 0
    for d in recycle_dirs():
        for path, st in _walk_files(d):
            if cutoff is not None and st.st_ctime > cutoff:
                continue
            try:
                os.unlink(path)  # unlinks symlinks themselves, never their targets
            except OSError as e:
                log.warning("Could not delete %s: %s", path, e)
                continue
            removed += 1
            freed += st.st_size
        # Remove folders left empty (keep the .recycle folder itself).
        for dirpath, _dirnames, _files in os.walk(d, topdown=False, followlinks=False):
            if dirpath != d and not os.path.islink(dirpath):
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass  # not empty
    return removed, freed


def run_scheduled():
    days = config.load()["recycle"]["retention_days"]
    if not days:
        return
    removed, freed = purge(older_than_days=days)
    if removed:
        events.log_event(None, "info", f"Recycle folders: removed {removed} file(s) older than {days} days "
                                       f"({freed / 1e6:.1f} MB)")


def _loop():
    time.sleep(FIRST_CHECK_SECONDS)
    while True:
        try:
            if config.exists():
                run_scheduled()
        except Exception:
            log.exception("Scheduled recycle cleanup failed")
        time.sleep(CHECK_EVERY_SECONDS)


_started = False


def start_scheduler():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True, name="recycle-cleanup").start()
