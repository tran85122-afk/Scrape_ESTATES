"""Kết nối PostgreSQL và tạo bảng dùng chung cho các scraper và giao diện.

Chuỗi kết nối lấy từ biến môi trường DATABASE_URL, hoặc từ file .env đặt cạnh code (không đưa lên git):
    DATABASE_URL=postgresql://scraper:MAT_KHAU@localhost:5433/bds
"""

import os

import psycopg
from psycopg.types.json import Jsonb

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = """
-- Trạng thái MỚI NHẤT của mỗi tin Chợ Tốt (một dòng/tin)
CREATE TABLE IF NOT EXISTS chotot_listings (
    list_id BIGINT PRIMARY KEY,
    ad_id BIGINT, url TEXT, danh_muc TEXT, loai TEXT, tieu_de TEXT,
    gia BIGINT, gia_text TEXT, dien_tich DOUBLE PRECISION,
    so_phong INTEGER, so_tang INTEGER, so_wc INTEGER, mat_duong BOOLEAN,
    duong TEXT, phuong TEXT, quan TEXT, tinh TEXT,
    lat DOUBLE PRECISION, lng DOUBLE PRECISION,
    mo_ta TEXT, anh JSONB, nguoi_dang TEXT, ngay_dang TIMESTAMP, raw JSONB,
    first_seen TIMESTAMP, last_seen TIMESTAMP,
    is_active SMALLINT NOT NULL DEFAULT 1, removed_at TIMESTAMP
);

-- Mỗi lần cào là một dòng; params lưu các tùy chọn đã dùng (lọc/giới hạn) để truy lại sau
CREATE TABLE IF NOT EXISTS chotot_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP, finished_at TIMESTAMP, status TEXT,
    n_found INTEGER, n_new INTEGER, n_removed INTEGER,
    params JSONB
);

-- LỊCH SỬ: mỗi khi một tin xuất hiện lần đầu, thay đổi, bị gỡ hoặc đăng lại thì ghi một dòng.
-- Nhờ đó truy ngược được: tin X vào ngày Y có giá bao nhiêu, đổi gì, gỡ lúc nào, ở lần cào nào.
CREATE TABLE IF NOT EXISTS chotot_history (
    id BIGSERIAL PRIMARY KEY,
    list_id BIGINT NOT NULL REFERENCES chotot_listings(list_id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES chotot_runs(id) ON DELETE SET NULL,
    at TIMESTAMP NOT NULL,
    su_kien TEXT NOT NULL CHECK (su_kien IN ('moi', 'thay_doi', 'go', 'dang_lai')),
    gia BIGINT, gia_text TEXT, dien_tich DOUBLE PRECISION, tieu_de TEXT,
    thay_doi JSONB,  -- với 'thay_doi': {"tên cột": [giá trị cũ, giá trị mới]}
    raw JSONB        -- JSON gốc của tin tại thời điểm đó
);
CREATE INDEX IF NOT EXISTS chotot_history_list_at ON chotot_history (list_id, at);

-- Bản dự phòng batdongsan.com.vn
CREATE TABLE IF NOT EXISTS batdongsan_listings (
    ma_tin TEXT PRIMARY KEY, danh_muc TEXT, url TEXT, tieu_de TEXT, dia_chi TEXT,
    lat DOUBLE PRECISION, lng DOUBLE PRECISION, gia TEXT, dien_tich TEXT, thong_so JSONB,
    ngay_dang TEXT, ngay_het_han TEXT, loai_tin TEXT, mo_ta TEXT, anh JSONB, lien_he TEXT,
    first_seen TIMESTAMP, last_seen TIMESTAMP, is_active SMALLINT NOT NULL DEFAULT 1, removed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS batdongsan_runs (
    id SERIAL PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP, status TEXT,
    n_found INTEGER, n_new INTEGER, n_removed INTEGER, n_errors INTEGER
);
"""


def load_env(path=None):
    """Đọc file .env (KEY=VALUE mỗi dòng) vào biến môi trường, không ghi đè biến đã có sẵn."""
    path = path or os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url():
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "Chưa cấu hình DATABASE_URL. Tạo file .env cạnh code với dòng:\n"
            "  DATABASE_URL=postgresql://scraper:MAT_KHAU@localhost:5433/bds\n"
            "(xem README, mục Cài đặt PostgreSQL)"
        )
    return url


def connect(**kwargs):
    """Mở kết nối PostgreSQL. kwargs truyền thẳng cho psycopg.connect (vd row_factory=dict_row)."""
    return psycopg.connect(database_url(), **kwargs)


def init_db(**kwargs):
    """Mở kết nối và tạo các bảng nếu chưa có (chạy nhiều lần không mất dữ liệu)."""
    con = connect(**kwargs)
    con.execute(SCHEMA)
    con.commit()
    return con


def jsonb(value):
    """Bọc dữ liệu Python (list/dict) để ghi vào cột JSONB."""
    return None if value is None else Jsonb(value)
