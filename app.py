"""
COCO.EDU — ĐỀ THI SIÊU KHÓ — single-file Flask backend
--------------------------------------------------------
Tính năng:
- 5 câu trắc nghiệm + 10 câu tự luận (nội dung thật, không placeholder).
- Mỗi IP chỉ làm bài đúng 1 lần.
- Bắt buộc toàn màn hình (Fullscreen API) trong lúc làm bài — thoát ra là bị khoá thao tác.
- Bắt buộc cấp quyền camera; hệ thống tự chụp ảnh nhanh tại các thời điểm NGẪU NHIÊN
  (không quay video) từ lúc bắt đầu tới lúc nộp bài, để người ra đề xem lại khi chấm.
- Nút ẩn ở góc màn hình -> nhập mã bảo mật -> xem NGAY danh sách bài làm (không cần
  rời trang) qua một API JSON nội bộ, và có thể mở trang chấm chi tiết từ đó.
- Không có đáp án mẫu nào được lưu trong hệ thống — người ra đề tự chấm đúng/sai từng câu.

THẬT LÒNG VỀ GIỚI HẠN KỸ THUẬT (đọc trước khi dùng thật):
- Phím F11 vật lý là hành vi cấp hệ điều hành/trình duyệt, JavaScript không thể chặn
  hay giả lập nó. Thay vào đó mình dùng Fullscreen API (requestFullscreen) — về hiệu
  quả cũng cho toàn màn hình và mình bắt được sự kiện thoát (kể cả thoát bằng Esc,
  bằng F11, hay bằng nút UI), nên tác dụng chống gian lận là tương đương.
- getUserMedia (camera) yêu cầu HTTPS (hoặc localhost) và trình duyệt LUÔN hiện hộp
  thoại xin quyền — không thể bật camera "âm thầm" từ phía web dù có code gì đi nữa.
  Vì vậy giao diện có màn hình thông báo rõ ràng trước khi xin quyền, để việc giám sát
  là công khai với người làm bài (và cũng vì trình duyệt bắt buộc phải vậy).
- Không thể chặn tuyệt đối Google Lens / chụp ảnh từ thiết bị thứ hai (điện thoại chụp
  lại màn hình chẳng hạn) — nằm ngoài khả năng của bất kỳ trang web nào. Camera giám sát
  + khoá toàn màn hình là hai lớp giảm thiểu tốt nhất có thể làm ở tầng trình duyệt.
"""

import json
import os
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)

app = Flask(__name__)
app.secret_key = os.environ.get("EXAM_SECRET_KEY", "doi-chuoi-nay-truoc-khi-deploy-that")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8MB — đủ cho ảnh snapshot nén nhỏ

ADMIN_CODE = "102980180280"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SUBMISSIONS_FILE = os.path.join(DATA_DIR, "submissions.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")  # bài đang làm, chưa nộp

# ---------------------------------------------------------------------------
# ĐỀ BÀI
# ---------------------------------------------------------------------------
MCQ_QUESTIONS = [
    {"id": "mcq1",
     "text": "Cho một chương trình P nhận chính source code của nó làm input. "
             "Phát biểu nào đúng về việc có tồn tại thuật toán tổng quát xác định "
             "P có dừng hay không, với MỌI P và MỌI input?",
     "options": {
        "A": "Luôn tồn tại vì máy tính hiện đại đủ mạnh để mô phỏng mọi trường hợp.",
        "B": "Không tồn tại thuật toán tổng quát nào — đây là hệ quả của lập luận "
             "đường chéo kiểu Cantor áp dụng lên không gian chương trình.",
        "C": "Tồn tại nhưng độ phức tạp là hàm mũ theo độ dài chương trình.",
        "D": "Chỉ tồn tại nếu P không được phép tự sửa đổi bản sao của chính nó."}},
    {"id": "mcq2",
     "text": "Một compressor C được gọi là 'hoàn hảo' nếu |C(x)| < |x| với MỌI chuỗi "
             "nhị phân x có độ dài hữu hạn (không mất mát, không từ điển ngoài). "
             "Theo nguyên lý chuồng bồ câu, điều gì đúng?",
     "options": {
        "A": "Compressor như vậy tồn tại nếu cho phép dùng từ điển ngoài.",
        "B": "Không thể tồn tại: số chuỗi độ dài n là 2^n, trong khi số chuỗi ngắn "
             "hơn n ít hơn 2^n, nên không đủ chỗ cho một ánh xạ đơn ánh.",
        "C": "Có thể tồn tại nếu C là một ánh xạ ngẫu nhiên.",
        "D": "Có thể tồn tại với xác suất tiệm cận 1 khi n tiến ra vô cùng."}},
    {"id": "mcq3",
     "text": "Một hệ tiên đề S đủ mạnh để biểu diễn số học Peano và nhất quán "
             "(consistent). Về khả năng S tự chứng minh tính nhất quán của chính nó, "
             "phát biểu nào đúng?",
     "options": {
        "A": "S có thể tự chứng minh tính nhất quán của chính nó bằng quy nạp.",
        "B": "S không thể tự chứng minh tính nhất quán của chính nó, trừ khi S "
             "thực ra không nhất quán.",
        "C": "S luôn có thể mở rộng thành một hệ vừa hoàn chỉnh vừa nhất quán.",
        "D": "Điều này chỉ đúng với logic bậc hai, không đúng với logic bậc nhất."}},
    {"id": "mcq4",
     "text": "Bạn có một trạng thái lượng tử chưa biết |ψ⟩. Phát biểu nào đúng về "
             "khả năng sao chép chính xác trạng thái này?",
     "options": {
        "A": "Có thể sao chép chính xác bằng một phép đo đơn giản.",
        "B": "Không tồn tại một toán tử unitary U nào có thể sao chép chính xác "
             "MỌI trạng thái lượng tử chưa biết.",
        "C": "Có thể sao chép nếu không gian trạng thái có từ 3 chiều trở lên.",
        "D": "Có thể sao chép với xác suất thành công cố định 50%."}},
    {"id": "mcq5",
     "text": "Với ≥3 phương án và ≥2 cử tri, ta muốn một hàm gộp thứ tự ưu tiên "
             "thoả đồng thời: không độc tài, hiệu quả Pareto, và độc lập với các "
             "phương án không liên quan (IIA). Điều gì đúng?",
     "options": {
        "A": "Luôn tồn tại vô số hàm thoả mãn cả ba điều kiện.",
        "B": "Không tồn tại hàm nào thoả mãn đồng thời cả ba điều kiện trên, "
             "trừ các trường hợp suy biến.",
        "C": "Tồn tại duy nhất một hàm — chính là phương pháp Borda count.",
        "D": "Chỉ tồn tại khi số phương án là một số chẵn."}},
]

ESSAY_QUESTIONS = [
    {"id": "essay1", "text":
        "Không được nêu tên bất kỳ định lý nổi tiếng nào: hãy tự lập luận từ đầu để "
        "chứng minh không tồn tại thuật toán A(P, n) luôn dừng và trả lời chính xác "
        "\"chương trình P có dừng sau đúng n bước hay không\", trong trường hợp P được "
        "phép tạo bản sao chính nó, sửa source code bản sao, rồi chạy bản sao đó."},
    {"id": "essay2", "text":
        "Cho số nguyên N có 10^100000 chữ số. Bạn chỉ được truy vấn từng chữ số theo "
        "vị trí, tối đa O(log N) lần truy vấn, không được dựng lại toàn bộ N. Bài toán "
        "\"N có phải số nguyên tố\" có giải được trong ràng buộc này không? Hãy chỉ rõ "
        "ranh giới giữa các biến thể của bài toán có thể và không thể giải được."},
    {"id": "essay3", "text":
        "Chứng minh không tồn tại một compressor nén được MỌI chuỗi nhị phân hữu hạn "
        "(không mất mát, không từ điển ngoài, không ngẫu nhiên hoá). Sau đó phân tích: "
        "nếu chỉ cần nén được 99,999999% các chuỗi độ dài n, nhưng đối thủ (adversary) "
        "được quyền chọn input, điều kiện bài toán thay đổi ra sao?"},
    {"id": "essay4", "text":
        "Xét một hệ tiên đề S đủ mạnh để mô phỏng mọi chứng minh trong chính nó. Từ các "
        "tiên đề cơ bản (không gọi tên định lý), hãy lập luận: S có thể chứng minh gì, "
        "không thể chứng minh gì, và khi nào S trở nên không nhất quán nếu cố chứng minh "
        "tính nhất quán của chính nó."},
    {"id": "essay5", "text":
        "Hai người chơi trên bàn cờ kích thước 10^1000 × 10^1000, không thể lưu hay duyệt "
        "toàn bộ trạng thái. Không cần tìm chiến lược thắng cụ thể — hãy phân tích câu hỏi "
        "\"trò chơi này có tồn tại chiến lược thắng hay không\" thuộc dạng quyết định được "
        "hay không, và giải thích vì sao."},
    {"id": "essay6", "text":
        "Cho hệ lượng tử hai trạng thái với toán tử unitary U chưa biết, biết U^37|ψ⟩ = |φ⟩ "
        "cùng một số thống kê đo được, không giả định cơ sở hay pha toàn cục. Phân tích: "
        "những khác biệt nào giữa các U ứng viên là khác biệt vật lý thật sự, và những "
        "khác biệt nào chỉ là khác biệt biểu diễn, không thể phân biệt bằng bất kỳ phép đo nào?"},
    {"id": "essay7", "text":
        "Có N quốc gia với các chỉ số không thể quy đổi về cùng đơn vị (GDP, khí thải, dân "
        "số, nợ công...). Không được cộng gộp chúng thành một utility function tuỳ tiện. Đề "
        "xuất một định nghĩa \"chính sách tối ưu toàn cầu\" không dựa trên utility function, "
        "và chỉ ra điều kiện khiến không tồn tại một chính sách tối ưu duy nhất."},
    {"id": "essay8", "text":
        "Ba đồng hồ A, B, C thoả |A(t)-B(t)|<3 và |B(t)-C(t)|<3, nhưng |A(t)-C(t)| không bị "
        "giới hạn trực tiếp; mỗi đồng hồ có độ trôi chưa biết trước. Hãy xây dựng một định "
        "nghĩa \"đồng thời\" (simultaneity) không phụ thuộc vào việc đồng hồ nào là chuẩn, "
        "và chứng minh định nghĩa đó bất biến dưới các phép đổi hệ quy chiếu hợp lệ."},
    {"id": "essay9", "text":
        "Đối thủ được xem toàn bộ source code, chứng minh đúng đắn và lịch sử thực thi của "
        "thuật toán bạn viết, trước khi chọn input. Hãy đề xuất (ở mức ý tưởng) một cách "
        "thiết kế khiến đối thủ không thể chọn input làm runtime vượt quá một hàm f(n) dưới "
        "cấp số mũ, và phân tích giới hạn bảo mật tối đa khi đối thủ được sửa thêm một bit "
        "trong bộ nhớ chương trình dựa trên chứng minh đó."},
    {"id": "essay10", "text":
        "Trình bày lập luận cho thấy không tồn tại một chương trình tổng quát tính chính "
        "xác độ phức tạp Kolmogorov của MỌI chuỗi nhị phân. Liên hệ kết quả này với bài "
        "toán ở câu tự luận số 1."},
]

ALL_QUESTION_IDS = [q["id"] for q in MCQ_QUESTIONS] + [q["id"] for q in ESSAY_QUESTIONS]

# ---------------------------------------------------------------------------
# Lưu trữ (JSON file, không cần DB)
# ---------------------------------------------------------------------------

def _ensure_store():
    os.makedirs(DATA_DIR, exist_ok=True)
    for f in (SUBMISSIONS_FILE, SESSIONS_FILE):
        if not os.path.exists(f):
            with open(f, "w", encoding="utf-8") as fh:
                json.dump({}, fh)


def _load(path):
    _ensure_store()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    _ensure_store()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def check_admin_code(req):
    """Chấp nhận mã qua header X-Admin-Code hoặc query string ?code=..."""
    code = req.headers.get("X-Admin-Code") or req.args.get("code", "")
    return code == ADMIN_CODE


# ---------------------------------------------------------------------------
# Trang thi
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    ip = get_client_ip()
    submissions = _load(SUBMISSIONS_FILE)
    if ip in submissions:
        return redirect(url_for("result"))
    return render_template(
        "index.html",
        mcq_questions=MCQ_QUESTIONS,
        essay_questions=ESSAY_QUESTIONS,
        client_ip=ip,
    )


@app.route("/api/session/start", methods=["POST"])
def session_start():
    ip = get_client_ip()
    submissions = _load(SUBMISSIONS_FILE)
    if ip in submissions:
        return jsonify({"ok": False, "error": "already_submitted"}), 409
    sessions = _load(SESSIONS_FILE)
    sessions[ip] = {
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "snapshots": [],
        "fullscreen_exits": 0,
    }
    _save(SESSIONS_FILE, sessions)
    return jsonify({"ok": True})


@app.route("/api/session/snapshot", methods=["POST"])
def session_snapshot():
    ip = get_client_ip()
    submissions = _load(SUBMISSIONS_FILE)
    if ip in submissions:
        return jsonify({"ok": False}), 409
    payload = request.get_json(silent=True) or {}
    image = payload.get("image", "")
    if not image.startswith("data:image/"):
        return jsonify({"ok": False, "error": "invalid_image"}), 400
    sessions = _load(SESSIONS_FILE)
    sess = sessions.get(ip)
    if not sess:
        return jsonify({"ok": False, "error": "no_session"}), 404
    sess["snapshots"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "image": image,
    })
    sessions[ip] = sess
    _save(SESSIONS_FILE, sessions)
    return jsonify({"ok": True, "count": len(sess["snapshots"])})


@app.route("/api/session/violation", methods=["POST"])
def session_violation():
    ip = get_client_ip()
    sessions = _load(SESSIONS_FILE)
    sess = sessions.get(ip)
    if sess:
        sess["fullscreen_exits"] = sess.get("fullscreen_exits", 0) + 1
        sessions[ip] = sess
        _save(SESSIONS_FILE, sessions)
        return jsonify({"ok": True, "count": sess["fullscreen_exits"]})
    return jsonify({"ok": False}), 404


@app.route("/submit", methods=["POST"])
def submit():
    ip = get_client_ip()
    submissions = _load(SUBMISSIONS_FILE)
    if ip in submissions:
        return redirect(url_for("result"))

    answers = {qid: request.form.get(qid, "").strip() for qid in ALL_QUESTION_IDS}

    sessions = _load(SESSIONS_FILE)
    sess = sessions.pop(ip, {})
    _save(SESSIONS_FILE, sessions)

    submissions[ip] = {
        "ip": ip,
        "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": sess.get("started_at"),
        "answers": answers,
        "snapshots": sess.get("snapshots", []),
        "fullscreen_exits": sess.get("fullscreen_exits", 0),
        "graded": False,
        "grades": {},
        "comments": {},
        "total_score": None,
    }
    _save(SUBMISSIONS_FILE, submissions)
    return redirect(url_for("result"))


@app.route("/result", methods=["GET"])
def result():
    ip = get_client_ip()
    submissions = _load(SUBMISSIONS_FILE)
    submission = submissions.get(ip)
    return render_template(
        "result.html",
        submission=submission,
        mcq_questions=MCQ_QUESTIONS,
        essay_questions=ESSAY_QUESTIONS,
    )


# ---------------------------------------------------------------------------
# API nội bộ cho bảng "xem nhanh" ngay trong trang (không cần rời trang)
# ---------------------------------------------------------------------------
@app.route("/api/admin/submissions", methods=["GET"])
def api_admin_submissions():
    if not check_admin_code(request):
        return jsonify({"ok": False, "error": "wrong_code"}), 403
    submissions = _load(SUBMISSIONS_FILE)
    out = []
    for s in sorted(submissions.values(), key=lambda s: s["submitted_at"], reverse=True):
        out.append({
            "ip": s["ip"],
            "submitted_at": s["submitted_at"],
            "graded": s["graded"],
            "total_score": s["total_score"],
            "snapshot_count": len(s.get("snapshots", [])),
            "fullscreen_exits": s.get("fullscreen_exits", 0),
        })
    return jsonify({"ok": True, "submissions": out})


@app.route("/api/admin/snapshots/<ip_key>", methods=["GET"])
def api_admin_snapshots(ip_key):
    if not check_admin_code(request):
        return jsonify({"ok": False, "error": "wrong_code"}), 403
    submissions = _load(SUBMISSIONS_FILE)
    s = submissions.get(ip_key)
    if not s:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "snapshots": s.get("snapshots", [])})


# ---------------------------------------------------------------------------
# Khu vực admin đầy đủ — chấm bài (đăng nhập bằng session)
# ---------------------------------------------------------------------------
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        code = request.form.get("code", "")
        next_url = request.form.get("next", "")
        if code == ADMIN_CODE:
            session["is_admin"] = True
            if next_url.startswith("/admin/grade/"):
                return redirect(next_url)
            return redirect(url_for("admin_dashboard"))
        flash("Mã bảo mật không đúng.")
        return redirect(url_for("admin_login"))
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    submissions = _load(SUBMISSIONS_FILE)
    rows = sorted(submissions.values(), key=lambda s: s["submitted_at"], reverse=True)
    return render_template("admin_dashboard.html", submissions=rows)


@app.route("/admin/grade/<ip_key>", methods=["GET", "POST"])
@admin_required
def admin_grade(ip_key):
    submissions = _load(SUBMISSIONS_FILE)
    submission = submissions.get(ip_key)
    if not submission:
        flash("Không tìm thấy bài làm của IP này.")
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        grades, comments = {}, {}
        correct_count = 0
        for qid in ALL_QUESTION_IDS:
            verdict = request.form.get(f"grade_{qid}", "unmarked")
            grades[qid] = verdict
            comments[qid] = request.form.get(f"comment_{qid}", "").strip()
            if verdict == "correct":
                correct_count += 1
        submission["grades"] = grades
        submission["comments"] = comments
        submission["total_score"] = correct_count
        submission["graded"] = True
        submissions[ip_key] = submission
        _save(SUBMISSIONS_FILE, submissions)
        flash("Đã lưu kết quả chấm.")
        return redirect(url_for("admin_dashboard"))

    return render_template(
        "admin_grade.html",
        submission=submission,
        mcq_questions=MCQ_QUESTIONS,
        essay_questions=ESSAY_QUESTIONS,
    )


@app.route("/admin/reset/<ip_key>", methods=["POST"])
@admin_required
def admin_reset(ip_key):
    submissions = _load(SUBMISSIONS_FILE)
    if ip_key in submissions:
        del submissions[ip_key]
        _save(SUBMISSIONS_FILE, submissions)
        flash(f"Đã xoá bài làm của IP {ip_key} — IP này có thể thi lại.")
    return redirect(url_for("admin_dashboard"))


if __name__ == "__main__":
    _ensure_store()
    app.run(host="0.0.0.0", port=5000, debug=True)
