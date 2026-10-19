"""Real-browser (Chromium) tests of the admin UI and its inline JavaScript."""
import os, re, sys
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get("BASE", "https://host.docker.internal:8443")
SHOTS = "/shots"
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    js_errors = []
    page.on("pageerror", lambda e: js_errors.append(f"{page.url}: {e}"))
    page.on("console", lambda m: m.type == "error" and js_errors.append(f"{page.url}: console: {m.text}"))

    page.goto(BASE + "/login")
    page.fill("#password", "demo-password")
    page.click("button:has-text('Log in')")
    check(page.url.rstrip("/") == BASE.rstrip("/"), f"login lands on the dashboard ({page.url})")

    for path in ["/", "/machines", "/machines/new", "/users", "/network", "/logs", "/logs?c=dnc", "/account",
                 "/machines/haas1", "/machines/haas1/edit", "/machines/siemens1", "/machines/fanuc1", "/machines/fanuc3",
                 "/machines/fadal1", "/machines/fanuc5", "/settings"]:
        page.goto(BASE + path)
        check(page.locator("h1").count() >= 1 and "Internal Server Error" not in page.content(), f"renders {path}")

    # machine form: fields follow the control type
    page.goto(BASE + "/machines/new")
    def visible(sel):
        return page.locator(sel).first.is_visible()
    page.select_option("#type", "haas-classic")
    check(visible("#password") and visible("#ip") and not visible("#serial_transport") and not visible("input[name=focas_port]"),
          "SMB type: IP + password shown, serial/FOCAS hidden")
    page.select_option("#type", "fanuc-rs232")
    page.select_option("#serial_transport", "usb")
    check(visible("#serial_device") and not visible("#ip") and not visible("#password"), "RS-232 + USB: port shown, IP/password hidden")
    page.select_option("#serial_transport", "tcp")
    check(visible("#ip") and visible("#serial_tcp_port") and not visible("#serial_device"), "RS-232 + device server: IP and TCP port shown")
    check("Device server IP" in page.locator("#ip-label").inner_text(), "IP label changes for a device server")
    page.select_option("#type", "fanuc-focas")
    check(visible("input[name=focas_port]") and visible("#ip") and not visible("#password"), "FOCAS: port/timeout shown, password hidden")
    page.screenshot(path=f"{SHOTS}/form-focas.png", full_page=True)

    # serial DNC page: live status and a send with progress
    page.goto(BASE + "/machines/fanuc3")
    expect(page.locator("#dnc-badge")).to_have_text(re.compile("idle|sending|waiting"), timeout=5000)
    check(True, "DNC status badge filled in by polling")
    row = page.locator("#programs tr", has_text="O0100.NC")
    row.locator("input[name=delay]").fill("0")
    row.locator("button:has-text('Send')").click()
    expect(page.locator("#dnc-badge")).to_have_text(re.compile("waiting|sending"), timeout=8000)
    check(True, "Send starts a transfer (badge waiting/sending)")
    expect(page.locator("#dnc-progress")).to_be_visible(timeout=8000)
    page.screenshot(path=f"{SHOTS}/dnc-sending.png", full_page=True)
    check(True, "progress bar shown while sending")
    page.fill("input[placeholder='Filter files...']", "zzz")
    check(not row.is_visible(), "file filter hides non-matching rows")
    page.fill("input[placeholder='Filter files...']", "")

    # FOCAS page: CNC status + directory loaded by JS; read and delete
    page.goto(BASE + "/machines/fanuc5")
    expect(page.locator("#cnc-programs")).to_contain_text("O1000", timeout=8000)
    check("connected" in page.locator("#cnc-status").inner_text(), "FOCAS status and directory loaded by JS")
    page.locator("#cnc-programs tr", has_text="O2050").locator("button:has-text('Read')").click()
    check("saved as received/O2050_" in page.content(), "Read from CNC saves to received/")
    page.once("dialog", lambda d: d.accept())
    expect(page.locator("#cnc-programs")).to_contain_text("O9001", timeout=8000)
    page.locator("#cnc-programs tr", has_text="O9001").locator("button:has-text('Delete')").click()
    check("write protected" in page.content(), "Delete error from CNC shown (simulated protect key)")
    page.screenshot(path=f"{SHOTS}/focas.png", full_page=True)

    # upload and delete (replacement for File Browser)
    page.goto(BASE + "/machines/haas1")
    open("/tmp/O6060.NC", "w").write("%\nO6060(BROWSER UPLOAD)\nM30\n%\n")
    page.locator("input[type=file]").set_input_files("/tmp/O6060.NC")
    page.locator("form[enctype] button:has-text('Upload')").click()
    check("O6060.NC uploaded" in page.content(), "upload through the browser")
    dialogs = []
    page.once("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.locator("tr", has_text="O6060.NC").locator("button:has-text('Delete')").click()
    check(dialogs == ["Move O6060.NC to the recycle folder?"] and "O6060.NC" in page.content(), "delete asks first; Cancel keeps the file")
    page.once("dialog", lambda d: d.accept())
    page.locator("tr", has_text="O6060.NC").locator("button:has-text('Delete')").click()
    check("moved to haas1/.recycle/" in page.content(), "delete after confirming")

    # view a program
    page.goto(BASE + "/machines/fanuc3")
    page.click("a:has-text('O0100.NC')")
    check("DEMO PART" in page.content() and page.locator("a:has-text('Download')").count() == 1, "program viewer with download")

    # narrow screen (tablet at the machine)
    m = b.new_context(ignore_https_errors=True, viewport={"width": 768, "height": 1024}, storage_state=ctx.storage_state())
    mp = m.new_page(); mp.goto(BASE + "/machines/haas1"); mp.screenshot(path=f"{SHOTS}/tablet-setup-sheet.png", full_page=True)
    check(mp.locator("h1").is_visible(), "renders on a tablet-sized screen")

    # theme switch: pick Dark, check the page really turns dark, switch back
    page.goto(BASE + "/settings")
    page.check("input[name=theme][value=dark]"); page.click("button:has-text('Save colours')")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    check(bg == "rgb(18, 22, 27)", f"Dark theme applied in the browser ({bg})")
    page.screenshot(path=f"{SHOTS}/dark-settings.png", full_page=True)
    page.check("input[name=theme][value=default]"); page.click("button:has-text('Save colours')")

    check(not js_errors, "no JavaScript errors" + (f": {js_errors[:3]}" if js_errors else ""))
    b.close()

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
