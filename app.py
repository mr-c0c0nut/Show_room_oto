"""
Long Biên Ford — Website (Render-ready v2, self-healing + debug)
Local:   python app.py  -> http://127.0.0.1:5000
Render:  Start command -> gunicorn app:app --workers 2 --threads 4 --timeout 120

Debug sau deploy:  mở  https://<app>.onrender.com/debug
"""
import os
from flask import Flask, send_from_directory, jsonify, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # tuyệt đối, không phụ thuộc CWD
VIDEO = "YTSave_YouTube_Media_ju-jnDopEAw_All-the-Reasons-Ford-Super-Duty_001_1080p.mp4"

app = Flask(__name__, static_folder=None)

MIME_EXTRA = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}


def resolve_file(name: str):
    """Tìm file: khớp tên chính xác trước, rồi khớp KHÔNG phân biệt hoa/thường."""
    p = os.path.join(BASE_DIR, name)
    if os.path.isfile(p):
        return name
    low = name.lower()
    for f in os.listdir(BASE_DIR):
        if f.lower() == low and os.path.isfile(os.path.join(BASE_DIR, f)):
            return f  # ví dụ upload 'Index.html' vẫn phục vụ '/index.html'
    return None


@app.after_request
def set_headers(resp):
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


@app.route("/")
def home():
    f = resolve_file("index.html")
    if not f:
        return (
            "<h2 style='font-family:sans-serif'>❌ Không tìm thấy index.html</h2>"
            f"<p style='font-family:sans-serif'>App đang chạy tại: <code>{BASE_DIR}</code></p>"
            "<p style='font-family:sans-serif'>File có trong thư mục này:</p>"
            f"<pre style='font-family:monospace'>{chr(10).join(sorted(os.listdir(BASE_DIR)))}</pre>"
            "<p style='font-family:sans-serif'>→ Mở <code>/debug</code> hoặc xem log Render để biết thêm.</p>"
        ), 404
    return send_from_directory(BASE_DIR, f, conditional=True)


@app.route("/<path:filename>")
def static_files(filename):
    if ".." in filename:  # chống path traversal
        abort(404)
    f = resolve_file(filename)
    if not f:
        abort(404)
    resp = send_from_directory(BASE_DIR, f, conditional=True)
    ext = os.path.splitext(f)[1].lower()
    if ext in MIME_EXTRA:
        resp.mimetype = MIME_EXTRA[ext]
    return resp


@app.route("/healthz")
def health():
    return jsonify(
        status="ok",
        base_dir=BASE_DIR,
        index=resolve_file("index.html") is not None,
        video=resolve_file(VIDEO) is not None,
        files=sorted(os.listdir(BASE_DIR)),
    ), 200


@app.route("/debug")
def debug():
    html = f"""
    <div style="font-family:monospace;background:#0b0e14;color:#9fd;padding:24px;min-height:100vh">
      <h2 style="color:#fff">🔍 Render Debug</h2>
      <p>BASE_DIR: {BASE_DIR}</p>
      <p>index.html: <b style="color:{'#6f6' if resolve_file('index.html') else '#f66'}">{resolve_file('index.html') or 'KHÔNG THẤY'}</b></p>
      <p>video intro: <b style="color:{'#6f6' if resolve_file(VIDEO) else '#f66'}">{resolve_file(VIDEO) or 'KHÔNG THẤY'}</b></p>
      <p>TẤT CẢ FILE TRONG THƯ MỤC:</p>
      <pre>{chr(10).join(sorted(os.listdir(BASE_DIR)))}</pre>
      <p style="color:#888">→ Nếu file của bạn có tên gần giống nhưng khác hoa/thường/đuôi .txt, đổi lại trên GitHub cho khớp.</p>
    </div>"""
    return html


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if not resolve_file("index.html"):
        print(f"⚠️  index.html không có trong {BASE_DIR}")
        print("   Files hiện có:", sorted(os.listdir(BASE_DIR)))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
