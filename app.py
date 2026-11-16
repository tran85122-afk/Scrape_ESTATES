"""Giao diện xem dữ liệu đã cào + nút "Chạy ngay" + đặt lịch chạy tự động (Windows Task Scheduler).

Chạy:  python app.py   rồi mở http://127.0.0.1:8000
"""

import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from chotot_scraper import init_db

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "data", "scrape.log")
SCRAPER = os.path.join(HERE, "chotot_scraper.py")
TASK_NAME = "BDS_Chotot_Weekly"  # tên cũ từ khi chỉ có lịch hằng tuần; giữ nguyên để không tạo task trùng
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_proc = None  # tiến trình cào đang chạy (bấm "Chạy ngay")
# Token chống CSRF: sinh ngẫu nhiên mỗi lần bật server, nhúng vào trang index.html.
# Mọi POST phải gửi lại token này trong header X-CSRF-Token. Trang web lạ không đọc được trang của mình
# (chính sách cùng nguồn + chặn Host lạ) nên không biết token -> không gửi lệnh hộ được.
CSRF_TOKEN = secrets.token_urlsafe(32)


def db():
    con = init_db()  # tạo data/ và các bảng nếu chưa cào lần nào -> giao diện hiện 0 tin thay vì lỗi 500
    con.row_factory = sqlite3.Row
    return con


def ps(cmd, env=None):
    """Chạy lệnh PowerShell, trả về stdout (dùng cho Task Scheduler, không phụ thuộc ngôn ngữ Windows).

    env: biến môi trường truyền thêm cho PowerShell. Dùng để đưa DỮ LIỆU (đường dẫn...) vào lệnh
    qua $env:TÊN thay vì ghép chuỗi, nên ký tự lạ trong dữ liệu không thể biến thành lệnh.
    """
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **(env or {})},
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "PowerShell lỗi")
    return r.stdout.strip()


def get_schedule():
    try:
        out = ps(f"""$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop
$i = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
[pscustomobject]@{{ state = "$($t.State)"; days = "$($t.Triggers[0].DaysOfWeek)";
  kind = "$($t.Triggers[0].CimClass.CimClassName)";
  start = "$($t.Triggers[0].StartBoundary)"; last = "$($i.LastRunTime)"; next = "$($i.NextRunTime)" }} | ConvertTo-Json""")
    except RuntimeError:
        return {"exists": False}
    s = json.loads(out)
    # DaysOfWeek là bitmask: Sunday=1, Monday=2, ... Saturday=64
    mask = int(s["days"] or 0)
    order = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    if "Daily" in s["kind"]:
        s["days"] = DAYS[:]  # hàng ngày = đủ 7 ngày
    else:
        s["days"] = [d for b, d in enumerate(order) if mask & (1 << b)]
    s["time"] = s["start"][11:16] if len(s["start"]) >= 16 else ""
    s["exists"] = True
    s["enabled"] = s["state"] != "Disabled"
    return s


def set_schedule(days, time_, enabled):
    """days: danh sách ngày trong tuần (tiếng Anh). Đủ 7 ngày -> lịch hàng ngày.

    Chống chèn lệnh PowerShell:
    - days, time_ (người dùng gửi lên) được kiểm tra chặt theo danh sách cố định / regex rồi mới ghép vào lệnh;
    - đường dẫn python, file scraper, thư mục KHÔNG ghép vào lệnh mà truyền qua biến môi trường
      ($env:BDS_PYTHON...), vì đường dẫn có thể chứa ký tự đặc biệt (vd dấu nháy cong ’ mà PowerShell
      cũng coi là dấu nháy đơn).
    """
    if not isinstance(days, list) or not isinstance(time_, str):
        raise ValueError("days phải là danh sách, time phải là chuỗi")
    days = [d for d in DAYS if d in days]  # chỉ giữ tên ngày nằm trong danh sách cố định
    if not days or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_):  # đúng dạng HH:MM, 00:00–23:59
        raise ValueError("Chọn ít nhất 1 ngày và giờ hợp lệ (HH:MM)")
    trigger = f"-Daily -At {time_}" if len(days) == 7 else f"-Weekly -DaysOfWeek {','.join(days)} -At {time_}"
    paths = {"BDS_PYTHON": sys.executable, "BDS_SCRIPT": SCRAPER, "BDS_DIR": HERE}
    ps(
        f"""$a = New-ScheduledTaskAction -Execute $env:BDS_PYTHON -Argument ('"' + $env:BDS_SCRIPT + '"') -WorkingDirectory $env:BDS_DIR
$tr = New-ScheduledTaskTrigger {trigger}
$st = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $tr -Settings $st -Force | Out-Null
if (-not {"$true" if enabled else "$false"}) {{ Disable-ScheduledTask -TaskName '{TASK_NAME}' | Out-Null }}""",
        env=paths,
    )
    return get_schedule()


def running():
    return _proc is not None and _proc.poll() is None


def api(path, q, body):
    global _proc
    con = db()
    if path == "/api/stats":
        r = con.execute("""SELECT COUNT(*) total, SUM(is_active) active, SUM(1-is_active) removed,
                           MAX(last_seen) last_seen FROM chotot_listings""").fetchone()
        by = con.execute("""SELECT loai, COUNT(*) n FROM chotot_listings WHERE is_active=1
                            GROUP BY loai ORDER BY n DESC""").fetchall()
        return {**dict(r), "by_type": [dict(x) for x in by]}
    if path == "/api/filters":
        return {
            "loai": [x[0] for x in con.execute("SELECT DISTINCT loai FROM chotot_listings ORDER BY 1")],
            "quan": [x[0] for x in con.execute("SELECT DISTINCT quan FROM chotot_listings ORDER BY 1")],
        }
    if path == "/api/listings":
        where, args = ["1=1"], []

        def g(k):  # lấy giá trị tham số k trên URL, không có thì ""
            return (q.get(k) or [""])[0]

        if g("q"):
            where.append("(tieu_de LIKE ? OR duong LIKE ? OR phuong LIKE ? OR mo_ta LIKE ?)")
            args += [f"%{g('q')}%"] * 4
        for k in ("loai", "quan"):
            if g(k):
                where.append(f"{k} = ?")
                args.append(g(k))
        if g("status") == "active":
            where.append("is_active = 1")
        elif g("status") == "removed":
            where.append("is_active = 0")
        elif g("status") == "new":
            # tin mới = xuất hiện lần đầu trong lần chạy trọn vẹn gần nhất
            where.append(
                "first_seen >= (SELECT started_at FROM chotot_runs WHERE status='done' "
                "AND n_removed IS NOT NULL ORDER BY id DESC LIMIT 1)"
            )
        if g("min_price"):
            where.append("gia >= ?")
            args.append(float(g("min_price")) * 1e6)
        if g("max_price"):
            where.append("gia <= ?")
            args.append(float(g("max_price")) * 1e6)
        w = " AND ".join(where)
        page, size = max(1, int(g("page") or 1)), 50
        total = con.execute(f"SELECT COUNT(*) FROM chotot_listings WHERE {w}", args).fetchone()[0]
        rows = con.execute(
            f"""SELECT list_id, tieu_de, loai, gia, gia_text, dien_tich, duong, phuong, quan,
                               lat, lng, ngay_dang, is_active FROM chotot_listings WHERE {w}
                               ORDER BY ngay_dang DESC LIMIT ? OFFSET ?""",
            [*args, size, (page - 1) * size],
        ).fetchall()
        return {"total": total, "page": page, "size": size, "items": [dict(r) for r in rows]}
    if path.startswith("/api/listing/"):
        r = con.execute("SELECT * FROM chotot_listings WHERE list_id=?", (int(path.rsplit("/", 1)[1]),)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["anh"] = json.loads(d["anh"] or "[]")
        d.pop("raw", None)
        return d
    if path == "/api/runs":
        return [dict(r) for r in con.execute("SELECT * FROM chotot_runs ORDER BY id DESC LIMIT 20")]
    if path == "/api/log":
        lines = []
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
                lines = [line for line in f.read().splitlines() if "[chotot]" in line][-30:]
        # lần chạy do Task Scheduler khởi động (không phải từ nút bấm); bỏ qua bản ghi treo quá 1 giờ
        sql_running = """SELECT COUNT(*) FROM chotot_runs WHERE status='running'
                         AND replace(started_at,'T',' ') > datetime('now','localtime','-1 hour')"""
        db_running = con.execute(sql_running).fetchone()[0]
        return {"running": running() or bool(db_running), "lines": lines}
    if path == "/api/run":
        if body is None:  # chỉ chạy khi POST, không chạy khi chỉ mở link (GET)
            return None
        if running():
            return {"ok": False, "msg": "Đang chạy rồi"}
        _proc = subprocess.Popen(
            [sys.executable, SCRAPER],
            cwd=HERE,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True}
    if path == "/api/schedule":
        if body is None:
            return get_schedule()
        return set_schedule(body["days"], body["time"], bool(body["enabled"]))
    return None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json; charset=utf-8"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")  # không lưu trang cũ (token cũ) sau khi bật lại server
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, body=None):
        # Chỉ phục vụ khi truy cập bằng địa chỉ localhost. Chặn "DNS rebinding": trang lạ trỏ tên miền
        # của nó về 127.0.0.1 để đọc dữ liệu / gửi lệnh (khi đó Host là tên miền lạ).
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return self._send(403, {"error": "forbidden host"})
        u = urlparse(self.path)
        # Không phục vụ file theo đường dẫn người dùng gửi lên: chỉ trả đúng MỘT file cố định (index.html),
        # mọi đường dẫn khác đi vào api() vốn chỉ khớp danh sách cố định -> không có path traversal (../).
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                page = f.read().replace(b"__CSRF_TOKEN__", CSRF_TOKEN.encode())  # nhúng token vào trang
                return self._send(200, page, "text/html; charset=utf-8")
        try:
            res = api(u.path, parse_qs(u.query), body)
        except ValueError as e:  # đầu vào sai (vd ?page=abc, giá không phải số, ngày giờ lịch sai) -> lỗi của client
            return self._send(400, {"error": f"Tham số không hợp lệ: {e}"})
        except Exception as e:
            return self._send(500, {"error": str(e)})
        if res is None:
            return self._send(404, {"error": "not found"})
        self._send(200, res)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        # Chống CSRF (trang web lạ gửi lệnh hộ tới server này), nhiều lớp:
        # 1) Origin phải là localhost; 2) phải là JSON (form HTML thường không gửi được);
        # 3) quan trọng nhất: phải kèm đúng token CSRF lấy từ trang index.html.
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return self._send(403, {"error": "forbidden origin"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(415, {"error": "cần Content-Type: application/json"})
        # compare_digest so sánh trong thời gian cố định, không lộ token qua thời gian phản hồi
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token") or "", CSRF_TOKEN):
            return self._send(403, {"error": "thiếu hoặc sai CSRF token"})
        n = int(self.headers.get("Content-Length", 0))
        self._handle(json.loads(self.rfile.read(n) or b"{}"))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console Windows mặc định cp1252
    print("Mở http://127.0.0.1:8000  (Ctrl+C để tắt)")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
