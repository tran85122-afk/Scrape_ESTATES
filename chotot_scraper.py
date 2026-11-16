"""Lấy tin CHO THUÊ để KINH DOANH tại Hà Nội từ Chợ Tốt Nhà (nhatot.com):
nhà mặt phố, nhà phố liền kề, văn phòng, mặt bằng kinh doanh, shophouse.

Dùng API JSON công khai mà chính trang nhatot.com gọi: mỗi request trả 50 tin, đã có sẵn
tọa độ, giá, diện tích, mô tả... -> không cần mở từng trang chi tiết, toàn bộ chỉ ~25 request.
Dữ liệu lưu vào PostgreSQL (xem db.py), kèm bảng lịch sử để truy ngược mọi thay đổi.

Chạy:
    python chotot_scraper.py              # cào toàn bộ
    python chotot_scraper.py --pages 1    # chạy thử: 1 trang (50 tin) mỗi danh mục
    python chotot_scraper.py --help       # xem các tùy chọn lọc / giới hạn
"""

import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from db import init_db, jsonb

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
# Tùy chọn --loai: mỗi loại là một truy vấn riêng tới API (API tự lọc theo house_type / commercial_type)
TYPES = {
    "mat-pho": (1020, {"house_type": 1}),
    "lien-ke": (1020, {"house_type": 4}),
    "shophouse": (1030, {"commercial_type": 1}),
    "officetel": (1030, {"commercial_type": 2}),
    "van-phong": (1030, {"commercial_type": 3}),
    "mat-bang": (1030, {"commercial_type": 4}),
}
# Các cột được theo dõi: đổi giá trị thì ghi một dòng 'thay_doi' vào chotot_history
TRACKED = ["gia", "gia_text", "dien_tich", "tieu_de", "mo_ta", "loai", "so_phong", "so_tang", "lat", "lng"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")  # log + bản xuất CSV (dữ liệu chính nằm trong PostgreSQL)


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


def row(a, cg):
    """Chuyển một tin JSON của Chợ Tốt thành một dòng của bảng chotot_listings."""
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
        "anh": a.get("images") or [],
        "nguoi_dang": a.get("account_name"),
        "ngay_dang": datetime.fromtimestamp(lt / 1000).replace(microsecond=0) if lt else None,
        "raw": a,
    }


def query_name(cg, extra):
    """Tên dễ đọc của một truy vấn, vd 'Nhà ở (Nhà mặt phố, mặt tiền)'."""
    label = HOUSE_TYPE.get(extra.get("house_type")) or COMMERCIAL_TYPE.get(extra.get("commercial_type"))
    return CATEGORIES[cg] + (f" ({label})" if label else "")


def matches(a, args):
    """Tin có thỏa các điều kiện lọc --quan, --gia-min/max (triệu/tháng), --dt-min/max (m²) không."""
    if args.quan:
        area = (a.get("area_name") or "").lower()
        if not any(q.lower() in area for q in args.quan):
            return False
    price, size = a.get("price"), a.get("size")
    if args.gia_min is not None and (price is None or price < args.gia_min * 1e6):
        return False
    if args.gia_max is not None and (price is None or price > args.gia_max * 1e6):
        return False
    if args.dt_min is not None and (size is None or size < args.dt_min):
        return False
    if args.dt_max is not None and (size is None or size > args.dt_max):
        return False
    return True


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Cào tin cho thuê kinh doanh tại Hà Nội từ Chợ Tốt. Không thêm tùy chọn = cào hết.",
    )
    g = ap.add_argument_group("giới hạn cào")
    g.add_argument("--pages", type=int, default=0, help="chỉ lấy N trang (50 tin/trang) mỗi nhóm, để chạy thử")
    g.add_argument("--max-tin", type=int, default=0, help="dừng khi đã lưu đủ N tin")
    f = ap.add_argument_group("giới hạn lọc")
    f.add_argument("--loai", nargs="+", choices=list(TYPES), help="chỉ lấy các loại này (mặc định: tất cả)")
    f.add_argument("--quan", nargs="+", help='chỉ lấy tin ở quận/huyện chứa chữ này, vd: --quan "Cầu Giấy" "Đống Đa"')
    f.add_argument("--gia-min", type=float, help="giá thuê tối thiểu, triệu/tháng")
    f.add_argument("--gia-max", type=float, help="giá thuê tối đa, triệu/tháng")
    f.add_argument("--dt-min", type=float, help="diện tích tối thiểu, m²")
    f.add_argument("--dt-max", type=float, help="diện tích tối đa, m²")
    ap.add_argument("--min-delay", type=float, default=6, help="nghỉ tối thiểu giữa 2 request, giây")
    ap.add_argument("--max-delay", type=float, default=12, help="nghỉ tối đa giữa 2 request, giây")
    return ap.parse_args(argv)


def changes(old, new):
    """So sánh các cột TRACKED giữa bản cũ (trong DB) và bản mới; trả về {cột: [cũ, mới]} cho cột bị đổi."""
    diff = {}
    for c in TRACKED:
        a, b = old.get(c), new.get(c)
        if isinstance(a, float) or isinstance(b, float):  # 50 và 50.0 là như nhau
            same = a is not None and b is not None and abs(float(a) - float(b)) < 1e-9 or a is None and b is None
        else:
            same = a == b
        if not same:
            diff[c] = [a, b]
    return diff


def save_listing(db, r, ts, run_id):
    """Upsert một tin và ghi lịch sử nếu tin mới / thay đổi / đăng lại. Trả về sự kiện ('moi', ...) hoặc None."""
    old = db.execute(
        f"SELECT is_active, {', '.join(TRACKED)} FROM chotot_listings WHERE list_id = %s", (r["list_id"],)
    ).fetchone()
    if old is None:
        event, diff = "moi", None
    else:
        old = dict(zip(["is_active", *TRACKED], old))
        diff = changes(old, r) or None
        event = "dang_lai" if old["is_active"] == 0 else ("thay_doi" if diff else None)

    values = {**r, "anh": jsonb(r["anh"]), "raw": jsonb(r["raw"])}
    cols = list(values)
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "list_id")
    db.execute(
        f"INSERT INTO chotot_listings ({', '.join(cols)}, first_seen, last_seen, is_active, removed_at) "
        f"VALUES ({', '.join(['%s'] * len(cols))}, %s, %s, 1, NULL) "
        f"ON CONFLICT (list_id) DO UPDATE SET {upd}, last_seen = EXCLUDED.last_seen, is_active = 1, removed_at = NULL",
        [*values.values(), ts, ts],
    )
    if event:
        db.execute(
            "INSERT INTO chotot_history (list_id, run_id, at, su_kien, gia, gia_text, dien_tich, tieu_de, thay_doi, raw) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (r["list_id"], run_id, ts, event, r["gia"], r["gia_text"], r["dien_tich"], r["tieu_de"],
             jsonb(diff), jsonb(r["raw"])),
        )  # fmt: skip
    return event


def export_csv(db):
    """Xuất bảng chotot_listings ra data/chotot_listings.csv (UTF-8 có BOM để Excel đọc đúng tiếng Việt)."""
    path = os.path.join(DATA_DIR, "chotot_listings.csv")
    os.makedirs(DATA_DIR, exist_ok=True)
    cur = db.execute("SELECT * FROM chotot_listings ORDER BY is_active DESC, danh_muc, loai, quan, list_id")
    names = [c.name for c in cur.description]
    keep = [i for i, n in enumerate(names) if n != "raw"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([names[i] for i in keep])
        for rec in cur:
            out = []
            for i in keep:
                v = rec[i]
                if isinstance(v, datetime):
                    v = v.isoformat(timespec="seconds")
                elif isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False)
                out.append(v)
            w.writerow(out)
    return path


def main():
    args = parse_args()
    # Có lọc hoặc giới hạn = chỉ lấy MỘT PHẦN tin -> không được coi tin vắng mặt là "đã gỡ"
    partial = bool(
        args.pages
        or args.max_tin
        or args.loai
        or args.quan
        or any(v is not None for v in (args.gia_min, args.gia_max, args.dt_min, args.dt_max))
    )
    queries = [TYPES[t] for t in args.loai] if args.loai else QUERIES

    db = init_db()
    run_id = db.execute(
        "INSERT INTO chotot_runs (started_at, status, params) VALUES (%s, 'running', %s) RETURNING id",
        (datetime.now().replace(microsecond=0), jsonb(vars(args))),
    ).fetchone()[0]
    db.commit()
    seen, n_new, n_skip, status = set(), 0, 0, "done"
    complete = True  # lấy đủ số tin API báo? (phòng khi API giới hạn số trang được lật)
    stop = False  # đã đủ --max-tin
    if partial:
        log(f"Chạy có lọc/giới hạn: {vars(args)} -> không kiểm tra tin đã gỡ lần này")
    try:
        for cg, extra in queries:
            if stop:
                break
            name = query_name(cg, extra)
            page, got, total = 0, 0, None
            while True:
                d = fetch(
                    {"region_v2": REGION_HANOI, "cg": cg, "st": "u", **extra, "limit": PAGE_SIZE, "o": page * PAGE_SIZE}
                )
                ads = d.get("ads") or []
                got += len(ads)
                total = d.get("total", total)
                ts = datetime.now().replace(microsecond=0)
                for a in ads:
                    if not matches(a, args):
                        n_skip += 1
                        continue
                    r = row(a, cg)
                    if r["list_id"] in seen:
                        continue
                    seen.add(r["list_id"])
                    n_new += save_listing(db, r, ts, run_id) == "moi"
                    if args.max_tin and len(seen) >= args.max_tin:
                        stop = True
                        break
                db.commit()
                page += 1
                log(f"{name}: trang {page}, {len(ads)} tin (tổng API báo {d.get('total')}) | đã có {len(seen)}")
                if stop:
                    log(f"  Đã đủ {args.max_tin} tin (--max-tin) -> dừng")
                    break
                time.sleep(random.uniform(args.min_delay, args.max_delay))
                if len(ads) < PAGE_SIZE or (args.pages and page >= args.pages):
                    break
            # Tin mới có thể được đăng giữa lúc lật trang nên cho lệch 2%
            if not partial and total and got < total * 0.98:
                complete = False
                log(f"  CẢNH BÁO: {name} chỉ lấy được {got}/{total} tin -> không đánh dấu tin đã gỡ lần này")
    except Exception as e:
        db.rollback()  # bỏ phần dở dang của trang đang ghi; các trang trước đã commit vẫn còn
        status = f"failed: {e}"
        log(status)

    # Chỉ đánh dấu "đã mất" khi lần chạy trọn vẹn: không lỗi, không lọc/giới hạn/chạy thử, lấy đủ tin.
    # Lần không trọn vẹn ghi n_removed = NULL để app.py không lấy nó làm mốc cho bộ lọc "tin mới".
    full_run = status == "done" and not partial and complete and bool(seen)
    n_removed = None
    if full_run:
        ts = datetime.now().replace(microsecond=0)
        removed = db.execute(
            "UPDATE chotot_listings SET is_active = 0, removed_at = %s "
            "WHERE is_active = 1 AND NOT (list_id = ANY(%s)) RETURNING list_id, gia, gia_text, dien_tich, tieu_de",
            (ts, list(seen)),
        ).fetchall()
        for list_id, gia, gia_text, dien_tich, tieu_de in removed:
            db.execute(
                "INSERT INTO chotot_history (list_id, run_id, at, su_kien, gia, gia_text, dien_tich, tieu_de) "
                "VALUES (%s, %s, %s, 'go', %s, %s, %s, %s)",
                (list_id, run_id, ts, gia, gia_text, dien_tich, tieu_de),
            )
        n_removed = len(removed)
    db.execute(
        "UPDATE chotot_runs SET finished_at = %s, status = %s, n_found = %s, n_new = %s, n_removed = %s WHERE id = %s",
        (datetime.now().replace(microsecond=0), status, len(seen), n_new, n_removed, run_id),
    )
    db.commit()
    log(
        f"Xong: {status} | tìm thấy {len(seen)} | mới {n_new} | "
        f"đã mất {n_removed if full_run else '– (không kiểm tra lần này)'}"
        + (f" | bỏ qua {n_skip} tin không khớp bộ lọc" if n_skip else "")
    )
    log(f"Đã xuất {export_csv(db)}")
    db.close()


if __name__ == "__main__":
    # console Windows / Task Scheduler mặc định cp1252 -> print tiếng Việt sẽ lỗi
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
