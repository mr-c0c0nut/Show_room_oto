"""
Long Biên Ford — Website (Render-ready)
Tự tìm file ở cả gốc repo lẫn thư mục templates/
Local:  python app.py  -> http://127.0.0.1:5000
Render: gunicorn app:app --workers 2 --threads 4 --timeout 120
Debug:  https://<app>.onrender.com/debug
"""
import os
from flask import Flask, send_file, jsonify, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
VIDEO = "YTSave_YouTube_Media_ju-jnDopEAw_All-the-Reasons-Ford-Super-Duty_001_1080p.mp4"

app = Flask(__name__, static_folder=None)

MIME_EXTRA = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}


def find_file(name: str):
    """Tìm file ở gốc repo trước, rồi đến templates/ (không phân biệt hoa/thường)."""
    low = name.lower()
    for d in (BASE_DIR, TEMPLATES_DIR):
        if not os.path.isdir(d):
            continue
        # khớp chính xác
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
        # khớp bỏ qua hoa/thường
        for f in os.listdir(d):
            if f.lower() == low and os.path.isfile(os.path.join(d, f)):
                return os.path.join(d, f)
    return None


@app.after_request
def set_headers(resp):
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Accept-Ranges"] = "bytes"  # cần cho video stream/seek
    return resp


@app.route("/")
def home():
    p = find_file("index.html")
    if not p:
        listing = sorted(set(os.listdir(BASE_DIR)) | set(
            os.listdir(TEMPLATES_DIR) if os.path.isdir(TEMPLATES_DIR) else []))
        return (
            "<h2 style='font-family:sans-serif'>❌ Không tìm thấy index.html</h2>"
            f"<p style='font-family:sans-serif'>Đã tìm ở: <code>{BASE_DIR}</code> và <code>{TEMPLATES_DIR}</code></p>"
            f"<pre style='font-family:monospace'>{chr(10).join(listing)}</pre>"
            "<p style='font-family:sans-serif'>→ Mở <code>/debug</code> để xem chi tiết.</p>"
        ), 404
    return send_file(p, conditional=True)


@app.route("/<path:filename>")
def static_files(filename):
    if ".." in filename:
        abort(404)
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
    return jsonify(
        status="ok",
        index=find_file("index.html") is not None,
        video=find_file(VIDEO) is not None,
    ), 200


@app.route("/debug")
def debug():
    def st(p):
        return f'<b style="color:#6f6">{p}</b>' if p else '<b style="color:#f66">KHÔNG THẤY</b>'
    files_root = sorted(os.listdir(BASE_DIR)) if os.path.isdir(BASE_DIR) else []
    files_tpl = sorted(os.listdir(TEMPLATES_DIR)) if os.path.isdir(TEMPLATES_DIR) else ["(không có thư mục templates)"]
    return f"""
    <div style="font-family:monospace;background:#0b0e14;color:#9fd;padding:24px;min-height:100vh">
      <h2 style="color:#fff">🔍 Debug</h2>
      <p>BASE_DIR: {BASE_DIR}</p>
      <p>TEMPLATES: {TEMPLATES_DIR}</p>
      <p>index.html → {st(find_file('index.html'))}</p>
      <p>video intro → {st(find_file(VIDEO))}</p>
      <p>--- Gốc repo ---</p><pre>{chr(10).join(files_root)}</pre>
      <p>--- templates/ ---</p><pre>{chr(10).join(files_tpl)}</pre>
    </div>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if not find_file("index.html"):
        print("⚠️ Không tìm thấy index.html! Kiểm tra /debug")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
