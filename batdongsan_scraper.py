"""DỰ PHÒNG: cào tin cho thuê (Hà Nội) trên batdongsan.com.vn bằng Playwright, lưu vào SQLite.

Trang dùng Cloudflare nên phải điều khiển Chrome thật (có cửa sổ) và chạy rất chậm (~2,5–3 giờ/lần).
Nguồn chính của dự án là chotot_scraper.py.

Chạy:
    python batdongsan_scraper.py                 # cào toàn bộ
    python batdongsan_scraper.py --limit 3       # chạy thử: chỉ 3 tin chi tiết
    python batdongsan_scraper.py --export        # xuất data/listings.csv từ DB
"""

import argparse
import csv
import json
import os
import random
import time
from datetime import datetime

from db import init_db, jsonb

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # chỉ cần khi cào thật; CI/test không cài playwright vẫn import được file này
    sync_playwright = None

BASE = "https://batdongsan.com.vn"
CATEGORIES = {
    "nha-mat-pho": "/cho-thue-nha-mat-pho-tp-ha-noi",
    "shophouse": "/cho-thue-shophouse-nha-pho-thuong-mai-tp-ha-noi",
    "van-phong": "/cho-thue-van-phong-tp-ha-noi",
}
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")  # log + bản xuất CSV (dữ liệu chính nằm trong PostgreSQL, xem db.py)
PROFILE_DIR = os.path.join(HERE, ".browser_profile")  # giữ cookie Cloudflare giữa các lần chạy

LIST_JS = """() => [...new Set([...document.querySelectorAll('a.js__product-link-for-product-id')]
                 .map(a => a.getAttribute('href')))]"""

DETAIL_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const q = s => document.querySelector(s);
  const specs = {};
  document.querySelectorAll('.re__pr-specs-content-item').forEach(e => {
    specs[clean(e.querySelector('.re__pr-specs-content-item-title')?.textContent)] =
      clean(e.querySelector('.re__pr-specs-content-item-value')?.textContent);
  });
  const cfg = {};
  document.querySelectorAll('.js__pr-config-item').forEach(e => {
    cfg[clean(e.querySelector('.title')?.textContent)] = clean(e.querySelector('.value')?.textContent);
  });
  const m = document.documentElement.innerHTML.match(/latitude:\s*([\d.]+),\s*longitude:\s*([\d.]+)/);
  const imgs = [...document.querySelectorAll('.re__media-thumbs img, .swiper-slide img, .re__pr-media-slide img')]
    .map(i => i.dataset.src || i.getAttribute('src')).filter(s => s && s.startsWith('http'));
  return {
    tieu_de: clean(q('h1')?.textContent),
    dia_chi: clean(q('.js__pr-address, .re__pr-short-description')?.textContent),
    lat: m ? parseFloat(m[1]) : null,
    lng: m ? parseFloat(m[2]) : null,
    thong_so: specs,
    cfg: cfg,
    mo_ta: (q('.re__detail-content')?.innerText || '').trim(),
    anh: [...new Set(imgs)],
    lien_he: clean(q('.re__contact-name, .js_contact-name')?.textContent),
  };
}"""


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "scrape.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def safe_eval(page, js, tries=10):
    """page.evaluate nhưng chịu được việc trang tự reload (Cloudflare qua challenge sẽ tải lại trang)."""
    for _ in range(tries):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            return page.evaluate(js)
        except Exception as e:
            if "context was destroyed" not in str(e) and "navigat" not in str(e):
                raise
            time.sleep(2)
    return page.evaluate(js)


def wait_cloudflare(page, timeout=45):
    """Chờ Cloudflare tự qua challenge (không tương tác). Trả về False nếu vẫn bị chặn."""
    end = time.time() + timeout
    while time.time() < end:
        # Trang challenge có biến window._cf_chl_opt (title đổi theo ngôn ngữ: "Just a moment" / "Chờ một chút")
        if not safe_eval(page, "() => !!window._cf_chl_opt"):
            return True
        time.sleep(2)
    return False


def goto(page, url, retries=3):
    for i in range(retries):
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # resp có thể là 403 của trang challenge; nếu challenge tự qua (hết biến _cf_chl_opt) thì coi là OK
        if wait_cloudflare(page):
            return True
        if i == retries - 1:
            break
        wait = 300 * (i + 1)
        log(f"  bị chặn/lỗi ({resp.status if resp else '?'}) – nghỉ {wait}s rồi thử lại")
        time.sleep(wait)
    raise BlockedError(f"Bị chặn {retries} lần liên tiếp tại {url} – dừng lần chạy, thử lại sau vài giờ")


class BlockedError(Exception):
    """Bị Cloudflare chặn liên tục -> dừng hẳn lần chạy, không cố thêm."""


_req_count = 0


def pause(args):
    """Nghỉ ngẫu nhiên giữa các request; cứ ~40 request thì nghỉ dài 2–4 phút như người dùng thật."""
    global _req_count
    _req_count += 1
    if _req_count % 40 == 0:
        rest = random.uniform(120, 240)
        log(f"  nghỉ dài {rest:.0f}s sau {_req_count} request")
        time.sleep(rest)
    else:
        time.sleep(random.uniform(args.min_delay, args.max_delay))


def collect_links(page, args):
    links, seen = [], set()
    for cat, path in CATEGORIES.items():
        for p in range(1, 100):
            url = f"{BASE}{path}{f'/p{p}' if p > 1 else ''}?vrs=1"
            if not goto(page, url):
                raise RuntimeError(f"Không mở được {url}")
            if p > 1 and f"/p{p}" not in page.url:  # trang vượt quá -> bị redirect về p1
                break
            hrefs = [h for h in safe_eval(page, LIST_JS) if h not in seen]
            log(f"{cat} p{p}: {len(hrefs)} tin mới")
            for h in hrefs:
                seen.add(h)
                links.append((cat, h))
            pause(args)
            if args.limit and len(links) >= args.limit:  # chạy thử: đủ link thì thôi, không duyệt hết danh sách
                return links
            if not hrefs:
                break
    return links


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="chỉ cào N tin chi tiết (chạy thử)")
    # Thực nghiệm 15/11/2026: ~30 request trong ~8 phút (delay 4–7s) đã bị 403 -> giữ dưới ~3 request/phút
    ap.add_argument("--min-delay", type=float, default=15)
    ap.add_argument("--max-delay", type=float, default=30)
    ap.add_argument("--headless", action="store_true", help="ẩn cửa sổ (thường bị Cloudflare chặn)")
    ap.add_argument("--export", action="store_true", help="chỉ xuất CSV từ DB")
    args = ap.parse_args()

    db = init_db()
    if args.export:
        export_csv(db)
        return
    if sync_playwright is None:
        raise SystemExit("Thiếu thư viện playwright: chạy  pip install -r requirements.txt  trong thư mục chứa code")

    now = datetime.now().replace(microsecond=0)
    run_id = db.execute(
        "INSERT INTO batdongsan_runs (started_at, status) VALUES (%s, 'running') RETURNING id", (now,)
    ).fetchone()[0]
    db.commit()
    n_new = n_err = 0
    seen_ids = []

    with sync_playwright() as pw:
        ctx = None
        try:
            # Mở Chrome nằm trong try: nếu Chrome lỗi, lần chạy vẫn được ghi "failed" thay vì kẹt ở "running"
            ctx = pw.chromium.launch_persistent_context(
                # Dùng Chrome cài sẵn (channel="chrome"): Chrome thật ít bị Cloudflare nghi ngờ hơn Chromium
                # tải kèm Playwright (bản này còn từng không khởi động được trên Windows: "spawn UNKNOWN").
                # Chế độ headless bị Cloudflare giữ lại ở trang challenge -> mặc định hiện cửa sổ.
                PROFILE_DIR,
                channel="chrome",
                headless=args.headless,
                locale="vi-VN",
                viewport={"width": 1366, "height": 850},
            )
            ctx.route(
                "**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_()
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            links = collect_links(page, args)
            log(f"Tổng {len(links)} tin trong danh sách")
            if args.limit:
                links = links[: args.limit]
            for i, (cat, href) in enumerate(links, 1):
                url = BASE + href
                try:
                    if not goto(page, url):
                        raise RuntimeError("bị chặn")
                    d = safe_eval(page, DETAIL_JS)
                    cfg = d["cfg"]
                    ma_tin = cfg.get("Mã tin") or href.rsplit("pr", 1)[-1]
                    if "Hà Nội" not in d["dia_chi"]:
                        log(f"  bỏ qua (không phải Hà Nội): {d['dia_chi']}")
                        pause(args)  # vẫn phải nghỉ: trang này đã được tải, bỏ qua mà không nghỉ dễ bị chặn
                        continue
                    seen_ids.append(ma_tin)
                    ts = datetime.now().replace(microsecond=0)
                    existed = db.execute("SELECT 1 FROM batdongsan_listings WHERE ma_tin = %s", (ma_tin,)).fetchone()
                    n_new += not existed
                    db.execute(
                        """
                    INSERT INTO batdongsan_listings(ma_tin, danh_muc, url, tieu_de, dia_chi, lat, lng, gia,
                        dien_tich, thong_so, ngay_dang, ngay_het_han, loai_tin, mo_ta, anh, lien_he,
                        first_seen, last_seen, is_active, removed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,NULL)
                    ON CONFLICT(ma_tin) DO UPDATE SET danh_muc=excluded.danh_muc, url=excluded.url,
                        tieu_de=excluded.tieu_de, dia_chi=excluded.dia_chi, lat=excluded.lat, lng=excluded.lng,
                        gia=excluded.gia, dien_tich=excluded.dien_tich, thong_so=excluded.thong_so,
                        ngay_dang=excluded.ngay_dang, ngay_het_han=excluded.ngay_het_han,
                        loai_tin=excluded.loai_tin, mo_ta=excluded.mo_ta, anh=excluded.anh,
                        lien_he=excluded.lien_he, last_seen=excluded.last_seen, is_active=1, removed_at=NULL
                    """,
                        (
                            ma_tin,
                            cat,
                            url,
                            d["tieu_de"],
                            d["dia_chi"],
                            d["lat"],
                            d["lng"],
                            d["thong_so"].get("Khoảng giá") or d["thong_so"].get("Mức giá"),
                            d["thong_so"].get("Diện tích"),
                            jsonb(d["thong_so"]),
                            cfg.get("Ngày đăng"),
                            cfg.get("Ngày hết hạn"),
                            cfg.get("Loại tin"),
                            d["mo_ta"],
                            jsonb(d["anh"]),
                            d["lien_he"],
                            ts,
                            ts,
                        ),
                    )
                    db.commit()
                    log(f"[{i}/{len(links)}] {ma_tin} | {d['lat']},{d['lng']} | {d['tieu_de'][:60]}")
                except BlockedError:
                    raise  # bị chặn -> dừng cả lần chạy, không gửi thêm request
                except Exception as e:  # lỗi 1 tin không làm hỏng cả lần chạy
                    db.rollback()  # PostgreSQL khóa giao dịch sau lỗi; rollback để các tin sau vẫn ghi được
                    n_err += 1
                    log(f"[{i}/{len(links)}] LỖI {url}: {e}")
                pause(args)
            status = "done"
        except Exception as e:
            db.rollback()  # bỏ phần ghi dở dang; các tin đã commit trước đó vẫn còn
            status = f"failed: {e}"
            log(status)
        finally:
            if ctx:
                ctx.close()

    # Chỉ đánh dấu tin "đã mất" khi chạy trọn vẹn (không phải chạy thử, không lỗi giữa chừng)
    n_removed = 0
    if status == "done" and not args.limit and seen_ids:
        ts = datetime.now().replace(microsecond=0)
        n_removed = db.execute(
            "UPDATE batdongsan_listings SET is_active = 0, removed_at = %s "
            "WHERE is_active = 1 AND NOT (ma_tin = ANY(%s))",
            (ts, seen_ids),
        ).rowcount
    db.execute(
        "UPDATE batdongsan_runs SET finished_at = %s, status = %s, n_found = %s, n_new = %s, n_removed = %s, "
        "n_errors = %s WHERE id = %s",
        (datetime.now().replace(microsecond=0), status, len(seen_ids), n_new, n_removed, n_err, run_id),
    )
    db.commit()
    log(f"Xong: {status} | tìm thấy {len(seen_ids)} | mới {n_new} | đã mất {n_removed} | lỗi {n_err}")
    export_csv(db)
    db.close()


def export_csv(db):
    path = os.path.join(DATA_DIR, "listings.csv")
    os.makedirs(DATA_DIR, exist_ok=True)
    cur = db.execute("SELECT * FROM batdongsan_listings ORDER BY is_active DESC, danh_muc, ma_tin")
    cols = [c.name for c in cur.description]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for rec in cur:
            w.writerow(
                [
                    v.isoformat(timespec="seconds")
                    if isinstance(v, datetime)
                    else json.dumps(v, ensure_ascii=False)
                    if isinstance(v, (list, dict))
                    else v
                    for v in rec
                ]
            )
    log(f"Đã xuất {path}")


if __name__ == "__main__":
    main()
