"""Settings → Appearance: colour themes, accent colour, logo upload."""
import base64, io, json, os, re
from werkzeug.security import generate_password_hash
from cncadmin import appearance, config, create_app

fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


config.set_admin_password_hash(generate_password_hash("correct-horse-1"))
app = create_app()
c = app.test_client(); c.environ_base["REMOTE_ADDR"] = "10.20.5.5"
anon = app.test_client(); anon.environ_base["REMOTE_ADDR"] = "10.20.5.6"
BASE = "https://localhost"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 200 + b"\xff\xd9"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def csrf():
    c.get(BASE + "/login")
    with c.session_transaction(base_url=BASE) as s:
        return s["csrf"]


def post(path, **data):
    data.setdefault("csrf", csrf())
    return c.post(BASE + path, data=data, follow_redirects=True)


def upload(data, name):
    return c.post(BASE + "/settings/logo", data={"csrf": csrf(), "logo": (io.BytesIO(data), name)},
                  content_type="multipart/form-data", follow_redirects=True)


def flashes(r):
    return re.findall(r'<div class="flash \w+">(.*?)</div>', r.get_data(as_text=True), re.S)


def root_css(html):
    return re.search(r":root \{(.*?)\}", html).group(1)


post("/login", username="admin", password="correct-horse-1")
raw = json.load(open(config.SITE_FILE)); raw.pop("appearance", None); json.dump(raw, open(config.SITE_FILE, "w"))
check(config.load()["appearance"]["theme"] == "default", "configs from before this feature load with the default theme")

page = c.get(BASE + "/").get_data(as_text=True)
check("--accent:#0b5cad;" in root_css(page) and "--header-bg:#16212c;" in root_css(page), "default theme rendered as CSS variables")
for key in appearance.THEMES:
    post("/settings/appearance", theme=key)
    css = root_css(c.get(BASE + "/").get_data(as_text=True))
    expect = {**appearance._LIGHT, **appearance.THEMES[key]["vars"]}
    check(f"--bg:{expect['bg']};" in css and f"--accent:{expect['accent']};" in css, f"theme '{key}' applied")
r = post("/settings/appearance", theme="dark;}body{display:none")
check(config.load()["appearance"]["theme"] == "contrast" and flashes(r), "unknown/injected theme name rejected")
post("/settings/appearance", theme="default", use_accent="on", accent="#ffeb3b")
css = root_css(c.get(BASE + "/").get_data(as_text=True))
check("--accent:#ffeb3b;" in css and "--accent-fg:#000000;" in css, "custom light accent gets black button text")
post("/settings/appearance", theme="default", use_accent="on", accent="#1a237e")
check("--accent-fg:#ffffff;" in root_css(c.get(BASE + "/").get_data(as_text=True)), "custom dark accent gets white button text")
for bad in ["red", "#12345", "#1234567", "#zzzzzz", "#123456;}body{display:none}"]:
    r = post("/settings/appearance", theme="default", use_accent="on", accent=bad)
    check(config.load()["appearance"]["accent"] == "#1a237e" and flashes(r), f"accent {bad!r} rejected")
post("/settings/appearance", theme="default", accent="#ff0000")
check(config.load()["appearance"]["accent"] == "", "unticking 'own accent' goes back to the theme colour")

# --- logo -------------------------------------------------------------------------
check(anon.get(BASE + "/branding/logo").status_code == 404, "no logo: 404")
r = upload(PNG, "logo.png")
a = config.load()["appearance"]
check(a["logo"] == "png" and a["logo_version"] > 0 and any("uploaded" in f for f in flashes(r)), "PNG logo uploaded")
lr = anon.get(BASE + "/branding/logo")
check(lr.status_code == 200 and lr.data == PNG and lr.mimetype == "image/png", "logo served without login (login page needs it)")
check(lr.headers.get("X-Content-Type-Options") == "nosniff" and lr.headers.get("Content-Security-Policy") == "default-src 'none'",
      "logo served with nosniff and a locked-down CSP")
page = c.get(BASE + "/").get_data(as_text=True)
check(f"/branding/logo?v={a['logo_version']}" in page, "logo in the header (cache-busted URL)")
check("/branding/logo" in anon.get(BASE + "/login").get_data(as_text=True), "logo on the login page")
check('class="print-only"' in c.get(BASE + "/machines/haas1").get_data(as_text=True), "logo on printed setup sheets")
for data, name, why in [(SVG, "logo.svg", "SVG (can carry scripts)"), (SVG, "logo.png", "SVG renamed to .png"),
                        (b"GIF89a" + b"\x00" * 20, "logo.gif", "GIF"), (b"", "empty.png", "empty file"),
                        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (appearance.LOGO_MAX_BYTES + 10), "big.png", "over 1 MB")]:
    r = upload(data, name)
    check(config.load()["appearance"]["logo"] == "png" and anon.get(BASE + "/branding/logo").data == PNG and flashes(r),
          f"rejected: {why}; previous logo kept")
r = upload(JPG, "photo.jpeg")
check(config.load()["appearance"]["logo"] == "jpg" and not os.path.exists(appearance.logo_path("png"))
      and anon.get(BASE + "/branding/logo").mimetype == "image/jpeg", "JPEG replaces the PNG (old file removed)")
r = post("/settings/logo/remove")
check(config.load()["appearance"]["logo"] == "" and anon.get(BASE + "/branding/logo").status_code == 404
      and not os.path.exists(appearance.logo_path("jpg")), "remove logo")
check(anon.post(BASE + "/settings/logo", data={"logo": (io.BytesIO(PNG), "x.png")}, content_type="multipart/form-data").status_code == 400,
      "upload without login/CSRF refused")
page = c.get(BASE + "/settings").get_data(as_text=True)
check("Colour theme" in page and "Upload logo" in page and page.count('name="theme"') == len(appearance.THEMES), "Appearance card renders all themes")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED", flush=True)
os._exit(1 if fails else 0)
