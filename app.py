"""
Long Biên Ford — Website (Render-ready)
Local:   python app.py  -> http://127.0.0.1:5000
Render:  start command -> gunicorn app:app
"""
import os
from flask import Flask, send_from_directory, jsonify

VIDEO = "YTSave_YouTube_Media_ju-jnDopEAw_All-the-Reasons-Ford-Super-Duty_001_1080p.mp4"

app = Flask(__name__)

# MIME đúng cho video để trình duyệt stream được
MIME_EXTRA = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

@app.after_request
def set_headers(resp):
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Accept-Ranges"] = "bytes"   # cần cho video seek/stream
    return resp


@app.route("/")
def home():
    return send_from_directory(".", "index.html", conditional=True)


@app.route("/<path:filename>")
def static_files(filename):
    # conditional=True -> tự xử lý Range request (206 Partial Content) cho video
    resp = send_from_directory(".", filename, conditional=True)
    ext = os.path.splitext(filename)[1].lower()
    if ext in MIME_EXTRA:
        resp.mimetype = MIME_EXTRA[ext]
    return resp


@app.route("/healthz")
def health():
    video_ok = os.path.exists(VIDEO)
    return jsonify(status="ok", video=video_ok), 200


if __name__ == "__main__":
    # Render truyền PORT qua biến môi trường; local mặc định 5000
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
