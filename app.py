"""
Long Biên Ford — Website + Security Shield (Render-ready)
============================================================
Tính năng bảo mật:
  1. ANTI-DDOS      : 1 IP > 20 request/phút  → chặn 2 PHÚT
  2. ANTI-HACK      : path độc / method lạ (POST, PUT, DELETE...) → chặn 30 TIẾNG + truy vết
  3. ANTI-TOOL      : User-Agent của tool hack/scrape (sqlmap, nikto, python-requests,
                      curl, scrapy...) → chặn 30 TIẾNG + truy vết
  4. TRUY VẾT       : ghi log JSON ra security.log (IP, thời gian, path, UA, headers)
  5. PANEL THEO DÕI : /admin/security?token=...  (đặt SECURITY_TOKEN trên Render)

Chạy local : python app.py  →  http://127.0.0.1:5000
Render     : gunicorn app:app --workers 1 --threads 8 --timeout 120
             (dùng 1 WORKER để bộ đếm IP chính xác giữa các request)
"""
import os
import time
import json
import threading
from collections import defaultdict, deque

from flask import Flask, request, jsonify, send_file, abort, Response

# ============================ CẤU HÌNH ============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
VIDEO = "YTSave_YouTube_Media_ju-jnDopEAw_All-the-Reasons-Ford-Super-Duty_001_1080p.mp4"
LOG_FILE = os.path.join(BASE_DIR, "security.log")

SECURITY_TOKEN = os.environ.get("SECURITY_TOKEN", "doi-token-nay-di")  # đổi trên Render!

# --- Ngưỡng ---
RATE_LIMIT   = 20            # request tối đa / phút
RATE_WINDOW  = 60            # giây
BLOCK_DDOS   = 2 * 60        # chặn 2 phút khi vượt rate
BLOCK_HACK   = 30 * 3600     # chặn 30 tiếng khi có dấu hiệu xâm nhập

# --- Các đường dẫn nguy hiểm (dấu hiệu dò quét / muốn ghi đè server) ---
DANGEROUS_PATTERNS = [
    "app.py", ".env", ".git", ".ssh", ".aws", "wp-admin", "wp-login", "wp-content",
    "phpmyadmin", "pma", "admin.php", "config.php", "shell", "cmd=", "exec",
    "eval(", "system(", "passwd", "/etc/", "id_rsa", "backup.sql", "dump",
    "upload", "filemanager", "cpanel", ".bak", ".sql", ".ini", ".yml", ".yaml",
    "xmlrpc.php", "actuator", "console/", "swagger.json", ".aspx", ".jsp",
]

# --- Ký tự chấm breakthrough: path traversal, SQLi, XSS ---
ATTACK_SIGNATURES = [
    "../", "..%2f", "..%5c", "%2e%2e", "..\\",
    "union select", "union%20select", "or 1=1", "' or ", "1=1--", "drop table",
    "<script", "%3cscript", "javascript:", "onerror=", "onload=",
    "${", "{{", "%24%7b",  # SSTI / template injection
]

# --- User-Agent đen: tool hack + tool scrape copy nội dung ---
BLACKLIST_UA = [
    "sqlmap", "nikto", "nmap", "masscan", "zgrab", "hydra", "dirbuster",
    "gobuster", "wfuzz", "ffuf", "dirsearch", "acunetix", "nessus",
    "python-requests", "python-urllib", "python-httpx", "aiohttp",
    "scrapy", "curl/", "wget", "httrack", "libwww", "java/", "go-http-client",
    "okhttp", "http-client", "node-fetch", "axios/", "postman",
    "dotbot", "semrush", "ahrefs", "mj12bot", "petalbot", "bytespider",
]

MIME_EXTRA = {".mp4": "video/mp4", ".m4v": "video/mp4",
              ".webm": "video/webm", ".mov": "video/quicktime"}

# ============================ APP ============================
app = Flask(__name__, static_folder=None)

# -------- Kho dữ liệu bảo mật (in-memory) --------
_req_hist:  dict[str, deque] = defaultdict(lambda: deque(maxlen=RATE_LIMIT * 2))
_blocked:   dict[str, dict]  = {}          # ip -> {until, reason, hits, first_seen}
_offenses:  dict[str, list]  = defaultdict(list)

_lock = threading.Lock()


def client_ip() -> str:
    """Render đặt sau proxy → IP thật nằm ở X-Forwarded-For (phần tử đầu)."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("X-Real-IP", request.remote_addr or "?") or "?"


def log_forensics(ip: str, reason: str, extra: str = ""):
    """Truy vết: ghi JSON-line ra file + in console."""
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "unix": int(time.time()),
        "ip": ip,
        "reason": reason,
        "method": request.method,
        "path": request.path[:300],
        "query": request.query_string.decode(errors="replace")[:300],
        "ua": request.headers.get("User-Agent", "")[:300],
        "referer": request.headers.get("Referer", "")[:200],
        "xff": request.headers.get("X-Forwarded-For", "")[:200],
        "extra": extra,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print(f"🚨 [SECURITY] {ip} — {reason} — {request.method} {request.path}")


def block(ip: str, seconds: int, reason: str, extra: str = ""):
    until = time.time() + seconds
    with _lock:
        prev = _blocked.get(ip)
        # giữ mức phạt nặng nhất nếu đã bị chặn trước đó
        if not prev or until > prev["until"]:
            _blocked[ip] = {
                "until": until, "reason": reason,
                "first_seen": prev["first_seen"] if prev else time.time(),
            }
        _blocked[ip]["hits"] = prev["hits"] + 1 if prev else 1
        _offenses[ip].append({"ts": int(time.time()), "reason": reason})
    log_forensics(ip, reason, extra)


def is_blocked(ip: str):
    with _lock:
        b = _blocked.get(ip)
        if b and time.time() < b["until"]:
            return b
        if b:  # hết hạn → gỡ
            _blocked.pop(ip, None)
    return None


# ============================ WAF (before_request) ============================
EXEMPT_PATHS = {"/healthz"}   # health check của Render không bị đếm


@app.before_request
def shield():
    ip = client_ip()
    path = request.path.lower()

    # 0) Health check — bỏ qua toàn bộ
    if path in EXEMPT_PATHS:
        return None

    # 1) Đã bị chặn?
    b = is_blocked(ip)
    if b:
        remain = int(b["until"] - time.time())
        if request.path.startswith("/healthz"):
            return jsonify(blocked=True, reason=b["reason"], remain_seconds=remain), 403
        return _block_page(ip, b["reason"], remain)

    # 2) METHOD lạ — web này chỉ đọc. POST/PUT/DELETE/PATCH = dấu hiệu muốn ghi đè server
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        block(ip, BLOCK_HACK, f"METHOD_BẠO_LỰC:{request.method}",
              "Cố gắng gửi lệnh thay đổi dữ liệu/máy chủ")
        return _block_page(ip, f"Phát hiện method nguy hiểm {request.method}", BLOCK_HACK)

    # 3) Path nguy hiểm — dò tìm file cấu hình, muốn sửa app.py, v.v.
    for pat in DANGEROUS_PATTERNS:
        if pat in path:
            block(ip, BLOCK_HACK, f"PATH_ĐỘC_HẠI:{pat}", "Dò quét file nhạy cảm / tìm cách can thiệp app.py")
            return _block_page(ip, "Phát hiện hành vi dò quét bất hợp pháp", BLOCK_HACK)

    # 4) Signature tấn công (traversal / SQLi / XSS / SSTI)
    full = (path + "?" + request.query_string.decode(errors="replace")).lower()
    for sig in ATTACK_SIGNATURES:
        if sig in full:
            block(ip, BLOCK_HACK, f"CHỈ_SỐ_TẤN_CÔNG:{sig}", "Injection / traversal / SSTI")
            return _block_page(ip, "Phát hiện payload tấn công", BLOCK_HACK)

    # 5) User-Agent đen — tool hack & tool copy nội dung
    ua = request.headers.get("User-Agent", "").lower()
    for bot in BLACKLIST_UA:
        if bot in ua:
            block(ip, BLOCK_HACK, f"UA_ĐEN:{bot}", "Tool hack / scrape copy nội dung")
            return _block_page(ip, "Client không được phép (tool tự động)", BLOCK_HACK)

    # 6) Không có User-Agent = bot thô sơ
    if not ua:
        block(ip, BLOCK_HACK, "UA_TRỐNG", "Request ẩn danh không UA")
        return _block_page(ip, "Yêu cầu ẩn danh bị từ chối", BLOCK_HACK)

    # 7) ANTI-DDOS: đếm sliding-window 60s
    now = time.time()
    with _lock:
        h = _req_hist[ip]
        h.append(now)
        while h and now - h[0] > RATE_WINDOW:
            h.popleft()
        count = len(h)
    if count > RATE_LIMIT:
        block(ip, BLOCK_DDOS, "FLOOD_VƯỢT_20REQ/PHÚT", f"count={count}")
        return _block_page(ip, "Gửi request quá nhanh (>20/phút)", BLOCK_DDOS)

    return None


def _block_page(ip: str, reason: str, remain: int) -> Response:
    mins = remain // 60
    secs = remain % 60
    remain_txt = f"{mins} phút {secs} giây" if mins else f"{secs} giây"
    html = f"""<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8">
<title>403 — Bị chặn</title><style>
body{{background:#04060b;color:#f2f5fa;font-family:'Segoe UI',Arial,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0}}
.c{{max-width:560px;padding:48px;border:1px solid rgba(255,255,255,.12);border-radius:22px;background:rgba(255,255,255,.04);backdrop-filter:blur(14px);text-align:center}}
h1{{font-size:72px;margin:0;color:#ff5d5d;font-weight:200}}
code{{background:rgba(255,255,255,.08);padding:2px 8px;border-radius:6px}}
.b{{margin-top:22px;font-size:13px;color:rgba(255,255,255,.45);letter-spacing:.2em;text-transform:uppercase}}
</style></head><body><div class="c">
<h1>403</h1>
<h2>Truy cập bị chặn</h2>
<p>Lý do: <b style="color:#ffb84f">{reason}</b></p>
<p>IP của bạn: <code>{ip}</code> đã được ghi lại và đưa vào danh sách giám sát.</p>
<p>⏳ Thời gian còn lại: <b>{remain_txt}</b></p>
<p class="b">Long Biên Ford — Security Shield</p>
</div></body></html>"""
    return Response(html, status=403, mimetype="text/html")


# ============================ CLEANUP THREAD ============================
def _cleanup():
    """Dọn bộ nhớ mỗi 60s: xóa IP đã hết hạn chặn + lịch sử cũ."""
    while True:
        time.sleep(60)
        now = time.time()
        with _lock:
            for ip in [k for k, v in _blocked.items() if now >= v["until"]]:
                _blocked.pop(ip, None)
            for ip in [k for k, q in _req_hist.items() if not q or now - q[-1] > 300]:
                _req_hist.pop(ip, None)

threading.Thread(target=_cleanup, daemon=True).start()


# ============================ PANEL THEO DÕI ============================
@app.route("/admin/security")
def security_panel():
    token = request.args.get("token", "")
    if token != SECURITY_TOKEN:
        ip = client_ip()
        block(ip, BLOCK_HACK, "DÒ_TOKEN_ADMIN", "Đoán token panel bảo mật")
        return _block_page(ip, "Truy cập trái phép khu vực quản trị", BLOCK_HACK)

    now = time.time()
    with _lock:
        rows = ""
        for ip, b in sorted(_blocked.items(), key=lambda x: -x[1]["until"]):
            remain = int(b["until"] - now)
            style = "color:#ff5d5d" if remain > 3600 else "color:#ffb84f"
            hist = " · ".join(o["reason"] for o in _offenses.get(ip, [])[-5:])
            rows += (f"<tr><td><code>{ip}</code></td><td>{b['reason']}</td>"
                     f"<td style='{style}'>{remain//3600}h {(remain%3600)//60}m</td>"
                     f"<td>{b['hits']}</td><td>{hist}</td></tr>")
        if not rows:
            rows = "<tr><td colspan='5'>✅ Không có IP nào bị chặn</td></tr>"

    tail = ""
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                lines = f.readlines()[-30:]
            tail = "".join(f"<div style='font-size:12px;color:#8fa'>{l.strip()}</div>"
                           for l in reversed(lines))
        except OSError:
            pass

    return f"""<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><title>Security Panel</title>
<style>body{{background:#04060b;color:#f2f5fa;font-family:monospace;padding:32px}}
table{{border-collapse:collapse;width:100%;margin:18px 0}}
td,th{{border:1px solid rgba(255,255,255,.15);padding:8px 12px;font-size:13px;text-align:left}}
th{{background:rgba(255,255,255,.06)}} h1{{font-weight:200}}</style></head><body>
<h1>🛡️ Security Panel — Long Biên Ford</h1>
<table><tr><th>IP</th><th>Lý do</th><th>Còn chặn</th><th>Số lần</th><th>Lịch sử vi phạm</th></tr>{rows}</table>
<h3>📜 30 dòng log mới nhất (truy vết)</h3>{tail or '<i>Chưa có log</i>'}
</body></html>"""


# ============================ ROUTES WEBSITE ============================
def find_file(name: str):
    low = name.lower()
    for d in (BASE_DIR, TEMPLATES_DIR):
        if not os.path.isdir(d):
            continue
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
        for f in os.listdir(d):
            if f.lower() == low and os.path.isfile(os.path.join(d, f)):
                return os.path.join(d, f)
    return None


@app.after_request
def set_headers(resp):
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"       # chặn nhúng iframe lừa đảo
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


@app.route("/")
def home():
    p = find_file("index.html")
    if not p:
        return "<h2>❌ Không tìm thấy index.html</h2><p>Mở /debug</p>", 404
    return send_file(p, conditional=True)


@app.route("/<path:filename>")
def static_files(filename):
    p = find_file(filename)
    if not p:
        abort(404)
    resp = send_file(p, conditional=True)
    ext = os.path.splitext(p)[1].lower()
    if ext in MIME_EXTRA:
        resp.mimetype = MIME_EXTRA[ext]
    return resp


@app.route("/healthz")
def health():
    return jsonify(status="ok", index=find_file("index.html") is not None,
                   video=find_file(VIDEO) is not None), 200


@app.route("/debug")
def debug():
    def st(p):
        return f'<b style="color:#6f6">{p}</b>' if p else '<b style="color:#f66">KHÔNG THẤY</b>'
    files = sorted(set(os.listdir(BASE_DIR)) |
                   set(os.listdir(TEMPLATES_DIR) if os.path.isdir(TEMPLATES_DIR) else []))
    return f"""<div style="font-family:monospace;background:#0b0e14;color:#9fd;padding:24px">
<h2>🔍 Debug</h2><p>index.html → {st(find_file('index.html'))}</p>
<p>video → {st(find_file(VIDEO))}</p><pre>{chr(10).join(files)}</pre></div>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🛡️  Security Shield ON — rate {RATE_LIMIT}/phút, log: {LOG_FILE}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
