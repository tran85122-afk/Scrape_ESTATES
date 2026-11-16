"""Lấy tin CHO THUÊ để KINH DOANH tại Hà Nội từ Chợ Tốt Nhà (nhatot.com):
nhà mặt phố, nhà phố liền kề, văn phòng, mặt bằng kinh doanh, shophouse.

Dùng API JSON công khai mà chính trang nhatot.com gọi: mỗi request trả 50 tin, đã có sẵn
tọa độ, giá, diện tích, mô tả... -> không cần mở từng trang chi tiết, toàn bộ chỉ ~25 request.

Chạy:
    python chotot_scraper.py              # cào toàn bộ
    python chotot_scraper.py --pages 1    # chạy thử: 1 trang (50 tin) mỗi danh mục
"""

import argparse
import csv
import json
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

API = "https://gateway.chotot.com/v1/public/ad-listing"
REGION_HANOI = 12000
PAGE_SIZE = 50
CATEGORIES = {1020: "Nhà ở", 1030: "Văn phòng, Mặt bằng kinh doanh"}
# Mục đích: thuê để KINH DOANH -> chỉ lấy mặt phố + liền kề (trong Nhà ở) và toàn bộ Văn phòng/Mặt bằng.
# Bỏ nhà ngõ/hẻm và biệt thự (chủ yếu để ở).
QUERIES = [
    (1020, {"house_type": 1}),  # Nhà mặt phố, mặt tiền
    (1020, {"house_type": 4}),  # Nhà phố liền kề
    (1030, {}),  # Văn phòng, Mặt bằng KD, Shophouse, Officetel
]
# Nhãn suy ra bằng cách lọc theo mã và đọc tiêu đề tin (15/11/2026)
HOUSE_TYPE = {1: "Nhà mặt phố, mặt tiền", 2: "Nhà biệt thự", 3: "Nhà ngõ, hẻm", 4: "Nhà phố liền kề"}
COMMERCIAL_TYPE = {1: "Shophouse", 2: "Officetel", 3: "Văn phòng", 4: "Mặt bằng kinh doanh"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
DB_PATH = os.path.join(DATA_DIR, "bds.sqlite")


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [chotot] {msg}"
    print(line, flush=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "scrape.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch(params, retries=3):
    url = API + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if i == retries - 1 or e.code not in (429, 500, 502, 503, 504):
                raise
            wait = 120 * (i + 1)
            log(f"  HTTP {e.code} – nghỉ {wait}s rồi thử lại")
            time.sleep(wait)
        except urllib.error.URLError:
            if i == retries - 1:
                raise
            time.sleep(30)


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS chotot_listings (
        list_id INTEGER PRIMARY KEY, ad_id INTEGER, url TEXT, danh_muc TEXT, loai TEXT,
        tieu_de TEXT, gia INTEGER, gia_text TEXT, dien_tich REAL, so_phong INTEGER, so_tang INTEGER,
        so_wc INTEGER, mat_duong INTEGER, duong TEXT, phuong TEXT, quan TEXT, tinh TEXT,
        lat REAL, lng REAL, mo_ta TEXT, anh TEXT, nguoi_dang TEXT, ngay_dang TEXT, raw TEXT,
        first_seen TEXT, last_seen TEXT, is_active INTEGER DEFAULT 1, removed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS chotot_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT,
        n_found INTEGER, n_new INTEGER, n_removed INTEGER
    );
    """)
    return db


def row(a, cg):
    lt = a.get("list_time")
    loai = HOUSE_TYPE.get(a.get("house_type")) or COMMERCIAL_TYPE.get(a.get("commercial_type"))
    return {
        "list_id": a["list_id"],
        "ad_id": a.get("ad_id"),
        "url": f"https://www.nhatot.com/{a['list_id']}.htm",
        "danh_muc": CATEGORIES[cg],
        "loai": loai,
        "tieu_de": a.get("subject"),
        "gia": a.get("price"),
        "gia_text": a.get("price_string"),
        "dien_tich": a.get("size"),
        "so_phong": a.get("rooms"),
        "so_tang": a.get("floors"),
        "so_wc": a.get("toilets"),
        "mat_duong": a.get("is_main_street"),
        "duong": a.get("street_name"),
        "phuong": a.get("ward_name"),
        "quan": a.get("area_name"),
        "tinh": a.get("region_name"),
        "lat": a.get("latitude"),
        "lng": a.get("longitude"),
        "mo_ta": a.get("body"),
        "anh": json.dumps(a.get("images") or [], ensure_ascii=False),
        "nguoi_dang": a.get("account_name"),
        "ngay_dang": datetime.fromtimestamp(lt / 1000).isoformat(timespec="seconds") if lt else None,
        "raw": json.dumps(a, ensure_ascii=False),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=0, help="chỉ lấy N trang mỗi danh mục (chạy thử)")
    ap.add_argument("--min-delay", type=float, default=6)
    ap.add_argument("--max-delay", type=float, default=12)
    args = ap.parse_args()

    db = init_db()
    run_id = db.execute(
        "INSERT INTO chotot_runs(started_at, status) VALUES (?, 'running')",
        (datetime.now().isoformat(timespec="seconds"),),
    ).lastrowid
    db.commit()
    seen, n_new, status = set(), 0, "done"
    complete = True  # lấy đủ số tin API báo? (phòng khi API giới hạn số trang được lật)
    try:
        for cg, extra in QUERIES:
            name = CATEGORIES[cg] + (f" ({HOUSE_TYPE[extra['house_type']]})" if extra else "")
            page, got, total = 0, 0, None
            while True:
                d = fetch(
                    {"region_v2": REGION_HANOI, "cg": cg, "st": "u", **extra, "limit": PAGE_SIZE, "o": page * PAGE_SIZE}
                )
                ads = d.get("ads") or []
                got += len(ads)
                total = d.get("total", total)
                ts = datetime.now().isoformat(timespec="seconds")
                for a in ads:
                    r = row(a, cg)
                    if r["list_id"] in seen:
                        continue
                    seen.add(r["list_id"])
                    n_new += not db.execute("SELECT 1 FROM chotot_listings WHERE list_id=?", (r["list_id"],)).fetchone()
                    cols = list(r) + ["first_seen", "last_seen"]
                    upd = ", ".join(f"{c}=excluded.{c}" for c in r if c != "list_id")
                    db.execute(
                        f"INSERT INTO chotot_listings({','.join(cols)}, is_active, removed_at) "
                        f"VALUES ({','.join('?' * len(cols))}, 1, NULL) "
                        f"ON CONFLICT(list_id) DO UPDATE SET {upd}, last_seen=excluded.last_seen, "
                        f"is_active=1, removed_at=NULL",
                        [*r.values(), ts, ts],
                    )
                db.commit()
                page += 1
                log(f"{name}: trang {page}, {len(ads)} tin (tổng API báo {d.get('total')}) | đã có {len(seen)}")
                time.sleep(random.uniform(args.min_delay, args.max_delay))
                if len(ads) < PAGE_SIZE or (args.pages and page >= args.pages):
                    break
            # Tin mới có thể được đăng giữa lúc lật trang nên cho lệch 2%
            if not args.pages and total and got < total * 0.98:
                complete = False
                log(f"  CẢNH BÁO: {name} chỉ lấy được {got}/{total} tin -> không đánh dấu tin đã gỡ lần này")
    except Exception as e:
        status = f"failed: {e}"
        log(status)

    # Chỉ đánh dấu "đã mất" khi lần chạy trọn vẹn: không lỗi, không phải chạy thử, lấy đủ tin.
    # Lần chạy thử ghi n_removed = NULL để app.py không lấy nó làm mốc cho bộ lọc "tin mới".
    full_run = status == "done" and not args.pages and complete and bool(seen)
    n_removed = None
    if full_run:
        ts = datetime.now().isoformat(timespec="seconds")
        db.execute("CREATE TEMP TABLE seen_ids(id INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO seen_ids VALUES (?)", [(i,) for i in seen])
        n_removed = db.execute(
            "UPDATE chotot_listings SET is_active=0, removed_at=? "
            "WHERE is_active=1 AND list_id NOT IN (SELECT id FROM seen_ids)",
            (ts,),
        ).rowcount
    db.execute(
        "UPDATE chotot_runs SET finished_at=?, status=?, n_found=?, n_new=?, n_removed=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), status, len(seen), n_new, n_removed, run_id),
    )
    db.commit()
    log(
        f"Xong: {status} | tìm thấy {len(seen)} | mới {n_new} | "
        f"đã mất {n_removed if full_run else '– (không kiểm tra lần này)'}"
    )

    path = os.path.join(DATA_DIR, "chotot_listings.csv")
    cur = db.execute("SELECT * FROM chotot_listings ORDER BY is_active DESC, danh_muc, loai, quan")
    cols = [c[0] for c in cur.description if c[0] != "raw"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for rec in cur:
            w.writerow([v for c, v in zip([c[0] for c in cur.description], rec) if c != "raw"])
    log(f"Đã xuất {path}")


if __name__ == "__main__":
    # console Windows / Task Scheduler mặc định cp1252 -> print tiếng Việt sẽ lỗi
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
