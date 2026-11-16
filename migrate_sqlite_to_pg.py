"""Chuyển dữ liệu cũ từ SQLite (data/bds.sqlite) sang PostgreSQL. Chạy MỘT lần sau khi cấu hình DATABASE_URL.

    python migrate_sqlite_to_pg.py                       # đọc data/bds.sqlite
    python migrate_sqlite_to_pg.py --sqlite D:/x/bds.sqlite

- Chuyển chotot_listings, chotot_runs và (nếu có) bảng listings/runs của batdongsan.
- Tạo lịch sử ban đầu cho mỗi tin: 'moi' tại first_seen, và 'go' tại removed_at nếu tin đã gỡ.
- Chạy lại nhiều lần không bị trùng: tin/lần chạy đã có trong PostgreSQL thì bỏ qua.
"""

import argparse
import json
import os
import sqlite3
import sys

from db import init_db, jsonb

HERE = os.path.dirname(os.path.abspath(__file__))


def ts(value):
    """SQLite lưu thời gian dạng chuỗi ISO; PostgreSQL tự đổi chuỗi này sang TIMESTAMP. Chuỗi rỗng -> NULL."""
    return value or None


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sqlite", default=os.path.join(HERE, "data", "bds.sqlite"), help="đường dẫn file SQLite cũ")
    args = ap.parse_args()
    if not os.path.exists(args.sqlite):
        raise SystemExit(f"Không thấy file {args.sqlite}")

    src = sqlite3.connect(args.sqlite)
    src.row_factory = sqlite3.Row
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    pg = init_db()

    # 1) Các lần chạy: giữ nguyên id để lịch sử khớp; sau đó chỉnh bộ đếm id của PostgreSQL
    n_runs = 0
    for r in src.execute("SELECT * FROM chotot_runs ORDER BY id") if "chotot_runs" in tables else []:
        n_runs += pg.execute(
            "INSERT INTO chotot_runs (id, started_at, finished_at, status, n_found, n_new, n_removed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (r["id"], ts(r["started_at"]), ts(r["finished_at"]), r["status"], r["n_found"], r["n_new"],
             r["n_removed"]),
        ).rowcount  # fmt: skip
    pg.execute("SELECT setval('chotot_runs_id_seq', GREATEST((SELECT MAX(id) FROM chotot_runs), 1))")

    # 2) Tin + lịch sử ban đầu
    n_tin = n_hist = 0
    for r in src.execute("SELECT * FROM chotot_listings") if "chotot_listings" in tables else []:
        added = pg.execute(
            """INSERT INTO chotot_listings (list_id, ad_id, url, danh_muc, loai, tieu_de, gia, gia_text, dien_tich,
                   so_phong, so_tang, so_wc, mat_duong, duong, phuong, quan, tinh, lat, lng, mo_ta, anh,
                   nguoi_dang, ngay_dang, raw, first_seen, last_seen, is_active, removed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (list_id) DO NOTHING""",
            (
                r["list_id"], r["ad_id"], r["url"], r["danh_muc"], r["loai"], r["tieu_de"], r["gia"], r["gia_text"],
                r["dien_tich"], r["so_phong"], r["so_tang"], r["so_wc"],
                None if r["mat_duong"] is None else bool(r["mat_duong"]),
                r["duong"], r["phuong"], r["quan"], r["tinh"], r["lat"], r["lng"], r["mo_ta"],
                jsonb(loads(r["anh"], [])), r["nguoi_dang"], ts(r["ngay_dang"]), jsonb(loads(r["raw"], None)),
                ts(r["first_seen"]), ts(r["last_seen"]), r["is_active"], ts(r["removed_at"]),
            ),
        ).rowcount  # fmt: skip
        if not added:
            continue
        n_tin += 1
        pg.execute(
            "INSERT INTO chotot_history (list_id, at, su_kien, gia, gia_text, dien_tich, tieu_de, raw) "
            "VALUES (%s, %s, 'moi', %s, %s, %s, %s, %s)",
            (r["list_id"], ts(r["first_seen"]), r["gia"], r["gia_text"], r["dien_tich"], r["tieu_de"],
             jsonb(loads(r["raw"], None))),
        )  # fmt: skip
        n_hist += 1
        if not r["is_active"] and r["removed_at"]:
            pg.execute(
                "INSERT INTO chotot_history (list_id, at, su_kien, gia, gia_text, dien_tich, tieu_de) "
                "VALUES (%s, %s, 'go', %s, %s, %s, %s)",
                (r["list_id"], ts(r["removed_at"]), r["gia"], r["gia_text"], r["dien_tich"], r["tieu_de"]),
            )
            n_hist += 1

    # 3) Bản dự phòng batdongsan (bảng listings/runs cũ), nếu có
    n_bds = 0
    for r in src.execute("SELECT * FROM listings") if "listings" in tables else []:
        n_bds += pg.execute(
            """INSERT INTO batdongsan_listings (ma_tin, danh_muc, url, tieu_de, dia_chi, lat, lng, gia, dien_tich,
                   thong_so, ngay_dang, ngay_het_han, loai_tin, mo_ta, anh, lien_he, first_seen, last_seen,
                   is_active, removed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (ma_tin) DO NOTHING""",
            (
                r["ma_tin"], r["danh_muc"], r["url"], r["tieu_de"], r["dia_chi"], r["lat"], r["lng"], r["gia"],
                r["dien_tich"], jsonb(loads(r["thong_so"], {})), r["ngay_dang"], r["ngay_het_han"], r["loai_tin"],
                r["mo_ta"], jsonb(loads(r["anh"], [])), r["lien_he"], ts(r["first_seen"]), ts(r["last_seen"]),
                r["is_active"], ts(r["removed_at"]),
            ),
        ).rowcount  # fmt: skip

    pg.commit()
    pg.close()
    print(f"Đã chuyển: {n_tin} tin Chợ Tốt, {n_runs} lần chạy, {n_hist} dòng lịch sử, {n_bds} tin batdongsan.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
