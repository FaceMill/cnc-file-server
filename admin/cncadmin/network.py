"""Office-network changes with automatic rollback.

Changing the office IP or allowed subnets can cut off the browser making the
change, so new settings are applied tentatively and reverted after
CONFIRM_SECONDS unless confirmed from the new address. The pending change is
persisted so a restart of the admin container mid-change also reverts.
"""
import copy
import json
import logging
import os
import threading
import time

from . import config, system

log = logging.getLogger("cncadmin")

CONFIRM_SECONDS = 120
APPLY_DELAY_SECONDS = 2  # let the HTTP response reach the browser first
RETRY_SECONDS = 60
PENDING_FILE = os.path.join(config.CONFIG_DIR, "pending-office.json")


class OfficeChange:
    def __init__(self):
        self.pending = None  # {"old": office, "new": office, "deadline": epoch}
        self.last_error = None
        self._timer = None
        self._generation = 0  # bumped by every start/clear, so stale apply threads can tell

    def status(self):
        if not self.pending:
            return None
        deadline = self.pending.get("deadline")
        left = max(0, int(deadline - time.time())) if deadline else None
        return {**self.pending, "seconds_left": left, "applying": deadline is None}

    def recover(self):
        """Revert a change left unconfirmed (or a failed revert) by a previous run."""
        try:
            with open(PENDING_FILE) as f:
                pending = json.load(f)
        except FileNotFoundError:
            return
        log.warning("Reverting unconfirmed office network change from a previous run")
        self.pending = pending
        # In the background: nmcli and container restarts can take longer than the web
        # server allows for starting up.
        threading.Thread(target=self.revert, daemon=True, args=(
            "A network change was left unconfirmed when the admin service restarted; "
            "previous settings restored.",)).start()

    def start(self, new_office):
        with config.lock:
            if self.pending:
                raise ValueError("Another network change is already waiting for confirmation")
            site = config.load()
            trial = copy.deepcopy(site)
            trial["office"] = new_office
            config.validate(trial)
            # The confirmation countdown starts once the change is actually applied.
            self.pending = {"old": site["office"], "new": trial["office"], "deadline": None}
            self.last_error = None
            self._generation += 1
            config._write_private(PENDING_FILE, json.dumps(self.pending))
            generation = self._generation
        threading.Thread(target=self._apply_new, args=(generation,), daemon=True).start()

    def _apply_new(self, generation):
        time.sleep(APPLY_DELAY_SECONDS)
        try:
            with config.lock:
                # Reverted (or replaced) while waiting for the lock: don't apply stale settings.
                if not self.pending or self._generation != generation:
                    return
                self._apply(self.pending["new"])
        except Exception as e:
            log.exception("Applying office network change failed")
            self.revert(f"Applying the change failed ({e}); previous settings restored.")
            return
        with config.lock:
            if not self.pending or self._generation != generation:
                return
            self.pending["deadline"] = time.time() + CONFIRM_SECONDS
            config._write_private(PENDING_FILE, json.dumps(self.pending))
            self._timer = threading.Timer(CONFIRM_SECONDS, self._timeout)
            self._timer.daemon = True
            self._timer.start()

    def _timeout(self):
        log.warning("Office network change not confirmed in %ss, reverting", CONFIRM_SECONDS)
        self.revert("Not confirmed in time; previous network settings restored.")

    def _apply(self, office):
        with config.lock:
            site = config.load()
            site["office"] = office
            system.nm_set_office(office)
            system.apply_site(site)

    def confirm(self):
        """Make the pending change permanent. False if there is nothing (yet) to confirm."""
        # Answer at once while the change is still being applied (that holds config.lock).
        if not self.pending or not self.pending.get("deadline"):
            return False
        with config.lock:
            if not self.pending or not self.pending.get("deadline"):
                return False
            if self._timer:
                self._timer.cancel()
            site = config.load()
            site["office"] = self.pending["new"]
            config.save(site)
            self._clear()
            return True

    def revert(self, message="Change reverted; previous settings restored."):
        with config.lock:
            if not self.pending:
                return
            if self._timer:
                self._timer.cancel()
            try:
                self._apply(self.pending["old"])
            except Exception as e:
                # Keep the pending change (and its file) so the revert is retried.
                log.exception("Reverting office network change failed; retrying in %ss", RETRY_SECONDS)
                self.last_error = (f"Restoring the previous network settings FAILED ({e}). "
                                   f"Retrying every {RETRY_SECONDS} s.")
                self._timer = threading.Timer(RETRY_SECONDS, self.revert, args=(message,))
                self._timer.daemon = True
                self._timer.start()
                return
            self.last_error = message
            self._clear()

    def _clear(self):
        self.pending = None
        self._timer = None
        self._generation += 1
        try:
            os.remove(PENDING_FILE)
        except FileNotFoundError:
            pass


office_change = OfficeChange()
