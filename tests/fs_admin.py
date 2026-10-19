"""Drive the real admin UI over HTTPS (through Caddy) like an office browser.
usage: fs_admin.py <action> [k=v ...]"""
import http.cookiejar, json, re, ssl, sys, urllib.parse, urllib.request

BASE = "https://cnc-files.test:8443"
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
jar = http.cookiejar.LWPCookieJar("/tmp/cookies")
try:
    jar.load(ignore_discard=True)
except FileNotFoundError:
    pass
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=ctx))


def get(path):
    with opener.open(BASE + path, timeout=60) as r:
        return r.read().decode()


def post(path, **data):
    html = get("/login" if path == "/login" else "/account")
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)
    body = urllib.parse.urlencode({"csrf": token, **data}).encode()
    with opener.open(BASE + path, data=body, timeout=120) as r:
        out = r.read().decode()
    jar.save(ignore_discard=True)
    return [re.sub(r"&#39;", "'", m) for m in re.findall(r'<div class="flash (?:ok|error)">(.*?)</div>', out, re.S)]


action, args = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])
if action == "login":
    print(post("/login", username="admin", password=args["password"]))
    jar.save(ignore_discard=True)
elif action == "post":
    path = args.pop("path")
    print(json.dumps(post(path, **args)))
elif action == "get":
    print(get(args["path"])[:int(args.get("n", 400))])
elif action == "password":
    html = get(f"/machines/{args['name']}")
    print(re.search(r"<th>Password</th><td class=\"mono\">([^<]+)<", html).group(1))
