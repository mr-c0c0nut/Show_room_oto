"""C0C0 GPT - bản bảo mật hơn, sẵn sàng deploy lên Render.

Local : pip install -r requirements.txt && python app.py
Render: Start Command  ->  gunicorn app:app --workers 1 --threads 8 --timeout 60
Biến môi trường: SECRET_KEY, ADMIN_PASSWORD, DATA_DIR (đường dẫn disk nếu có)
"""
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, make_response, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

logging.basicConfig(level=logging.INFO)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE)
DB = os.path.join(DATA_DIR, "c0c0gpt.db")
UPLOADS = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

IS_PROD = bool(os.environ.get("RENDER"))  # Render tự đặt biến này
ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "12345678910"  # chỉ dùng khi chưa đặt ADMIN_PASSWORD
MAX_TEXT = 4000
MAX_IMG = 5 * 1024 * 1024
DEFAULT_TITLE = "Cuộc trò chuyện mới"
MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def load_secret():
    k = os.environ.get("SECRET_KEY")
    if k:
        return k
    p = os.path.join(DATA_DIR, ".secret_key")
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    k = secrets.token_hex(32)
    with open(p, "w") as f:
        f.write(k)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    logging.warning("Chưa đặt SECRET_KEY - đã tự tạo khóa ngẫu nhiên. Nên đặt SECRET_KEY trên Render.")
    return k


app = Flask(__name__)
app.secret_key = load_secret()
app.config.update(
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PROD,
    SESSION_COOKIE_NAME="__Host-c0c0" if IS_PROD else "c0c0",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)
if IS_PROD:  # Render đứng sau reverse proxy
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


# ---------- DB ----------
@contextmanager
def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE COLLATE NOCASE NOT NULL,
            pw TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS chats(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT DEFAULT '',
            image TEXT,
            tokens INTEGER DEFAULT 0,
            created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate(key TEXT NOT NULL, ts REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id, id);
        CREATE INDEX IF NOT EXISTS idx_msg_img ON messages(image);
        CREATE INDEX IF NOT EXISTS idx_chat_user ON chats(user_id, updated);
        CREATE INDEX IF NOT EXISTS idx_rate ON rate(key, ts);
        """)
        pw = os.environ.get("ADMIN_PASSWORD")
        row = c.execute("SELECT id FROM users WHERE username=?", (ADMIN_USER,)).fetchone()
        if not row:
            c.execute("INSERT OR IGNORE INTO users(username,pw,is_admin) VALUES(?,?,1)",
                      (ADMIN_USER, generate_password_hash(pw or DEFAULT_ADMIN_PASS)))
        elif pw:  # đổi ADMIN_PASSWORD trên Render -> mật khẩu admin đổi theo
            c.execute("UPDATE users SET pw=?, is_admin=1 WHERE id=?", (generate_password_hash(pw), row["id"]))
    if not os.environ.get("ADMIN_PASSWORD"):
        logging.warning("Chưa đặt ADMIN_PASSWORD - admin đang dùng mật khẩu mặc định yếu!")


init_db()
DUMMY_HASH = generate_password_hash(secrets.token_hex(8))


# ---------- helpers ----------
def now():
    return time.time()


def token_count(seconds):
    """(số giây x 10 token) chia 2"""
    return int(seconds * 10 / 2)


def client_ip():
    return request.remote_addr or "?"


def too_many(key, limit, window):
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM rate WHERE key=? AND ts>?", (key, now() - window)).fetchone()[0]
    return n >= limit


def hit(key):
    with db() as c:
        c.execute("INSERT INTO rate(key,ts) VALUES(?,?)", (key, now()))
        c.execute("DELETE FROM rate WHERE ts<?", (now() - 86400,))


def clear_hits(key):
    with db() as c:
        c.execute("DELETE FROM rate WHERE key=?", (key,))


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user():
            return jsonify(error="Chưa đăng nhập"), 401
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        u = current_user()
        if not u:
            return jsonify(error="Chưa đăng nhập"), 401
        if not u["is_admin"]:
            return jsonify(error="Chỉ admin"), 403
        return f(*a, **k)
    return w


def wait_seconds(c, chat_id):
    """None nếu không có tin nào đang chờ; ngược lại là số giây người dùng đã chờ."""
    last = c.execute("SELECT role FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
    if not last or last["role"] != "user":
        return None
    a = c.execute("SELECT MAX(id) m FROM messages WHERE chat_id=? AND role='assistant'", (chat_id,)).fetchone()["m"] or 0
    t = c.execute("SELECT MIN(created) t FROM messages WHERE chat_id=? AND role='user' AND id>?",
                  (chat_id, a)).fetchone()["t"]
    return max(0.0, now() - t)


def sniff(head):
    """Nhận dạng ảnh bằng chữ ký file, không tin tên file / content-type người dùng gửi."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def save_image(f):
    if not f or not f.filename:
        return None
    head = f.stream.read(16)
    ext = sniff(head)
    if not ext:
        raise ValueError("Chỉ hỗ trợ ảnh PNG, JPG, GIF, WEBP")
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_IMG:
        raise ValueError("Ảnh tối đa 5MB")
    name = uuid.uuid4().hex + "." + ext
    f.save(os.path.join(UPLOADS, name))
    return name


def remove_file(name):
    if name and re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|gif|webp)", name):
        try:
            os.remove(os.path.join(UPLOADS, name))
        except OSError:
            pass


def get_chat(c, chat_id, user):
    ch = c.execute("SELECT ch.*, u.username FROM chats ch JOIN users u ON u.id=ch.user_id WHERE ch.id=?",
                   (chat_id,)).fetchone()
    if not ch or (ch["user_id"] != user["id"] and not user["is_admin"]):
        return None
    return ch


def add_message(c, chat_id, role, content, image, tokens=0):
    t = now()
    c.execute("INSERT INTO messages(chat_id,role,content,image,tokens,created) VALUES(?,?,?,?,?,?)",
              (chat_id, role, content, image, tokens, t))
    c.execute("UPDATE chats SET updated=? WHERE id=?", (t, chat_id))


# ---------- bảo mật chung ----------
@app.before_request
def csrf_guard():
    """Chặn request ghi dữ liệu đến từ trang web khác (CSRF)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        o = request.headers.get("Origin") or request.headers.get("Referer")
        if o and urlparse(o).netloc != request.host:
            return jsonify(error="Yêu cầu không hợp lệ"), 403


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    if IS_PROD:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


@app.errorhandler(413)
def too_big(e):
    return jsonify(error="Dữ liệu gửi lên quá lớn (ảnh tối đa 5MB)"), 413


@app.errorhandler(404)
def not_found(e):
    return jsonify(error="Không tìm thấy"), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify(error="Lỗi máy chủ, thử lại sau"), 500


# ---------- pages ----------
@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/")
def index():
    nonce = secrets.token_urlsafe(16)
    with open(os.path.join(BASE, "index.html"), encoding="utf-8") as f:
        html = f.read().replace("<script>", f'<script nonce="{nonce}">').replace("<style>", f'<style nonce="{nonce}">')
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "img-src 'self' blob: data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'")
    return resp


@app.route("/uploads/<name>")
@login_required
def uploads(name):
    m = re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|gif|webp)", name)
    if not m:
        abort(404)
    u = current_user()
    with db() as c:
        row = c.execute("SELECT ch.user_id FROM messages m JOIN chats ch ON ch.id=m.chat_id WHERE m.image=?",
                        (name,)).fetchone()
    if not row or (row["user_id"] != u["id"] and not u["is_admin"]):
        abort(404)
    resp = send_from_directory(UPLOADS, name, mimetype=MIME[m.group(1)])
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


# ---------- auth ----------
@app.route("/api/me")
def me():
    u = current_user()
    if not u:
        return jsonify(user=None)
    return jsonify(user={"id": u["id"], "username": u["username"], "is_admin": bool(u["is_admin"])})


@app.route("/api/register", methods=["POST"])
def register():
    d = request.get_json(silent=True) or {}
    name, pw = str(d.get("username") or "").strip(), str(d.get("password") or "")
    ip = client_ip()
    if too_many(f"reg:{ip}", 5, 3600):
        return jsonify(error="Tạo tài khoản quá nhiều lần, thử lại sau"), 429
    if not re.fullmatch(r"[A-Za-z0-9_.]{3,20}", name):
        return jsonify(error="Tên 3-20 ký tự: chữ, số, _ hoặc ."), 400
    if name.lower().startswith(("admin", "root", "system", "support")):
        return jsonify(error="Tên này không được phép dùng"), 400
    if not 8 <= len(pw) <= 128:
        return jsonify(error="Mật khẩu từ 8 đến 128 ký tự"), 400
    try:
        with db() as c:
            cur = c.execute("INSERT INTO users(username,pw) VALUES(?,?)", (name, generate_password_hash(pw)))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="Tên này đã có người dùng"), 400
    hit(f"reg:{ip}")
    session.clear()
    session.permanent = True
    session["uid"] = uid
    return jsonify(ok=True)


@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json(silent=True) or {}
    name, pw = str(d.get("username") or "").strip()[:40], str(d.get("password") or "")[:200]
    k_ip, k_user = f"login:{client_ip()}", f"login:{client_ip()}:{name.lower()}"
    if too_many(k_ip, 20, 600) or too_many(k_user, 5, 600):
        return jsonify(error="Thử sai quá nhiều lần, vui lòng đợi vài phút"), 429
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    ok = check_password_hash(u["pw"] if u else DUMMY_HASH, pw) and u is not None  # luôn băm để tránh lộ user tồn tại
    if not ok:
        hit(k_ip)
        hit(k_user)
        return jsonify(error="Sai tên hoặc mật khẩu"), 400
    clear_hits(k_user)
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    return jsonify(ok=True)


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(ok=True)


# ---------- chats (người dùng) ----------
@app.route("/api/chats", methods=["GET", "POST"])
@login_required
def chats():
    u = current_user()
    with db() as c:
        if request.method == "POST":
            if c.execute("SELECT COUNT(*) FROM chats WHERE user_id=?", (u["id"],)).fetchone()[0] >= 100:
                return jsonify(error="Tối đa 100 đoạn chat, hãy xóa bớt"), 400
            cur = c.execute("INSERT INTO chats(user_id,title,updated) VALUES(?,?,?)", (u["id"], DEFAULT_TITLE, now()))
            return jsonify(id=cur.lastrowid)
        rows = c.execute("SELECT id,title FROM chats WHERE user_id=? ORDER BY updated DESC", (u["id"],)).fetchall()
        return jsonify(chats=[{"id": r["id"], "title": r["title"], "pending": wait_seconds(c, r["id"]) is not None}
                              for r in rows])


@app.route("/api/chats/<int:cid>", methods=["GET", "DELETE"])
@login_required
def chat(cid):
    u = current_user()
    with db() as c:
        ch = get_chat(c, cid, u)
        if not ch:
            return jsonify(error="Không tìm thấy"), 404
        if request.method == "DELETE":
            if ch["user_id"] != u["id"]:
                return jsonify(error="Không có quyền"), 403
            imgs = [r["image"] for r in c.execute(
                "SELECT image FROM messages WHERE chat_id=? AND image IS NOT NULL", (cid,))]
            c.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
            c.execute("DELETE FROM chats WHERE id=?", (cid,))
            for n in imgs:
                remove_file(n)
            return jsonify(ok=True)
        msgs = c.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY id", (cid,)).fetchall()
        w = wait_seconds(c, cid)
        return jsonify(
            chat={"id": cid, "title": ch["title"], "username": ch["username"]},
            messages=[{"id": m["id"], "role": m["role"], "content": m["content"],
                       "image": ("/uploads/" + m["image"]) if m["image"] else None,
                       "tokens": m["tokens"]} for m in msgs],
            waiting=w is not None, wait_seconds=w or 0)


@app.route("/api/chats/<int:cid>/send", methods=["POST"])
@login_required
def send(cid):
    u = current_user()
    text = (request.form.get("text") or "").strip()
    if len(text) > MAX_TEXT:
        return jsonify(error=f"Tin nhắn tối đa {MAX_TEXT} ký tự"), 400
    if too_many(f"send:{u['id']}", 30, 60):
        return jsonify(error="Bạn gửi quá nhanh, chậm lại một chút"), 429
    with db() as c:
        ch = get_chat(c, cid, u)
        if not ch or ch["user_id"] != u["id"]:
            return jsonify(error="Không tìm thấy"), 404
        try:
            img = save_image(request.files.get("image"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not text and not img:
            return jsonify(error="Tin nhắn trống"), 400
        add_message(c, cid, "user", text, img)
        if ch["title"] == DEFAULT_TITLE:
            c.execute("UPDATE chats SET title=? WHERE id=?", ((text[:40] or "Hình ảnh"), cid))
    hit(f"send:{u['id']}")
    return jsonify(ok=True)


# ---------- admin ----------
@app.route("/api/admin/inbox")
@admin_required
def inbox():
    out = []
    with db() as c:
        rows = c.execute("""
            SELECT ch.id, ch.title, ch.updated, u.username,
              (SELECT content FROM messages WHERE chat_id=ch.id ORDER BY id DESC LIMIT 1) last_content,
              (SELECT image FROM messages WHERE chat_id=ch.id ORDER BY id DESC LIMIT 1) last_image
            FROM chats ch JOIN users u ON u.id=ch.user_id
            WHERE EXISTS(SELECT 1 FROM messages WHERE chat_id=ch.id)
            ORDER BY ch.updated DESC LIMIT 300""").fetchall()
        for r in rows:
            w = wait_seconds(c, r["id"])
            out.append({"id": r["id"], "title": r["title"], "username": r["username"],
                        "preview": (r["last_content"] or ("[Hình ảnh]" if r["last_image"] else ""))[:120],
                        "pending": w is not None, "wait_seconds": w or 0, "updated": r["updated"]})
    out.sort(key=lambda x: (not x["pending"], -x["wait_seconds"] if x["pending"] else -x["updated"]))
    return jsonify(chats=out)


@app.route("/api/admin/chats/<int:cid>/reply", methods=["POST"])
@admin_required
def reply(cid):
    u = current_user()
    text = (request.form.get("text") or "").strip()
    if len(text) > MAX_TEXT * 2:
        return jsonify(error="Tin nhắn quá dài"), 400
    with db() as c:
        if not get_chat(c, cid, u):
            return jsonify(error="Không tìm thấy"), 404
        try:
            img = save_image(request.files.get("image"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not text and not img:
            return jsonify(error="Tin nhắn trống"), 400
        w = wait_seconds(c, cid)
        add_message(c, cid, "assistant", text, img, token_count(w) if w is not None else 0)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
