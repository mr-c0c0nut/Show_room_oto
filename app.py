"""C0C0 GPT - giao diện giống ChatGPT, nhưng người trả lời là ADMIN (bạn).
Chạy:  pip install flask   ->   python app.py   ->   http://localhost:5000
Đặt index.html cùng thư mục với app.py.
"""
import os, re, sqlite3, time, uuid
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "c0c0gpt.db")
UPLOADS = os.path.join(BASE, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

ADMIN_USER = "admin"
ADMIN_PASS = "12345678910"
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "c0c0-gpt-doi-key-nay-di")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8MB


# ---------- DB ----------
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE COLLATE NOCASE NOT NULL,
            pw TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS chats(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,           -- 'user' | 'assistant'
            content TEXT DEFAULT '',
            image TEXT,
            tokens INTEGER DEFAULT 0,
            created REAL NOT NULL
        );
        """)
        if not c.execute("SELECT 1 FROM users WHERE username=?", (ADMIN_USER,)).fetchone():
            c.execute("INSERT INTO users(username,pw,is_admin) VALUES(?,?,1)",
                      (ADMIN_USER, generate_password_hash(ADMIN_PASS)))


init_db()
DEFAULT_TITLE = "Cuộc trò chuyện mới"


# ---------- helpers ----------
def now():
    return time.time()


def token_count(seconds):
    """(số giây x 10 token) chia 2"""
    return int(seconds * 10 / 2)


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
        if not u or not u["is_admin"]:
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


def save_image(f):
    if not f or not f.filename:
        return None
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXT:
        raise ValueError("Chỉ hỗ trợ ảnh png, jpg, gif, webp")
    name = uuid.uuid4().hex + "." + ext
    f.save(os.path.join(UPLOADS, name))
    return name


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


# ---------- pages ----------
@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.route("/uploads/<name>")
@login_required
def uploads(name):
    return send_from_directory(UPLOADS, name)


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
    name, pw = (d.get("username") or "").strip(), d.get("password") or ""
    if not re.fullmatch(r"[A-Za-z0-9_.]{3,20}", name):
        return jsonify(error="Tên 3-20 ký tự: chữ, số, _ hoặc ."), 400
    if len(pw) < 6:
        return jsonify(error="Mật khẩu tối thiểu 6 ký tự"), 400
    try:
        with db() as c:
            cur = c.execute("INSERT INTO users(username,pw) VALUES(?,?)", (name, generate_password_hash(pw)))
            session["uid"] = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="Tên này đã có người dùng"), 400
    return jsonify(ok=True)


@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json(silent=True) or {}
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", ((d.get("username") or "").strip(),)).fetchone()
    if not u or not check_password_hash(u["pw"], d.get("password") or ""):
        return jsonify(error="Sai tên hoặc mật khẩu"), 400
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
            c.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
            c.execute("DELETE FROM chats WHERE id=?", (cid,))
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
            WHERE EXISTS(SELECT 1 FROM messages WHERE chat_id=ch.id)""").fetchall()
        for r in rows:
            w = wait_seconds(c, r["id"])
            out.append({"id": r["id"], "title": r["title"], "username": r["username"],
                        "preview": r["last_content"] or ("[Hình ảnh]" if r["last_image"] else ""),
                        "pending": w is not None, "wait_seconds": w or 0, "updated": r["updated"]})
    out.sort(key=lambda x: (not x["pending"], -x["wait_seconds"] if x["pending"] else -x["updated"]))
    return jsonify(chats=out)


@app.route("/api/admin/chats/<int:cid>/reply", methods=["POST"])
@admin_required
def reply(cid):
    u = current_user()
    text = (request.form.get("text") or "").strip()
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
    app.run(host="0.0.0.0", port=5000, debug=True)
