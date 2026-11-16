# Phương pháp kéo dữ liệu mặt bằng cho thuê (Hà Nội)

Tài liệu cho team: dữ liệu lấy từ đâu, lấy bằng cách nào, và **chạy hàm/lệnh nào** để kéo dữ liệu.

---

## 1. Tóm tắt

| | |
|---|---|
| **Nguồn chính** | Chợ Tốt Nhà (nhatot.com), qua API JSON công khai |
| **Phạm vi** | Tin **cho thuê để kinh doanh** tại **Hà Nội**: nhà mặt phố, nhà phố liền kề, văn phòng, mặt bằng kinh doanh, shophouse, officetel |
| **Dữ liệu mỗi tin** | Tọa độ (lat/lng), giá, diện tích, số phòng/tầng/WC, đường/phường/quận, mô tả, ảnh, ngày đăng, link gốc |
| **Quy mô** | ~1.200 tin, ~25 request, ~4–5 phút mỗi lần kéo |
| **Lưu trữ** | PostgreSQL (database `bds`): trạng thái mới nhất + **lịch sử thay đổi** từng tin; kèm bản CSV `data/chotot_listings.csv` |
| **Lệnh chạy** | `python chotot_scraper.py` |
| **Nguồn dự phòng** | batdongsan.com.vn (`batdongsan_scraper.py`): chậm và dễ bị chặn, xem mục 6 |

---

## 2. Chạy thế nào

Yêu cầu: Python 3.10 trở lên, PostgreSQL, và thư viện `psycopg` (`pip install -r requirements.txt`). Cài đặt database: xem README, mục Cài đặt.

```bash
cd Scrape_ESTATES     # thư mục gốc của repo

# Chạy thử: chỉ 1 trang (50 tin) mỗi nhóm, ~30 giây
python chotot_scraper.py --pages 1

# Kéo toàn bộ (dùng hằng ngày)
python chotot_scraper.py

# Tùy chỉnh khoảng nghỉ giữa các request (giây). Không nên đặt dưới 5.
python chotot_scraper.py --min-delay 8 --max-delay 15

# Giao diện xem dữ liệu, chạy ngay, đặt lịch: mở http://127.0.0.1:8000
python app.py
```

Trước khi chạy lần đầu: cài PostgreSQL, tạo database và file `.env` (xem README, mục Cài đặt). Giao diện `app.py` đọc dữ liệu từ PostgreSQL.

### Giới hạn lọc và giới hạn cào

Mặc định (không thêm tùy chọn) là **cào hết** tin trong phạm vi: Hà Nội, cho thuê, loại phù hợp kinh doanh. Có thể thu hẹp:

| Tùy chọn | Ý nghĩa | Ví dụ |
|---|---|---|
| `--max-tin N` | Dừng khi đã lưu đủ N tin | `--max-tin 100` |
| `--pages N` | Chỉ lấy N trang (50 tin/trang) mỗi nhóm, để chạy thử | `--pages 1` |
| `--loai ...` | Chỉ lấy các loại: `mat-pho`, `lien-ke`, `shophouse`, `officetel`, `van-phong`, `mat-bang` (API tự lọc, nên ít request hơn) | `--loai van-phong mat-bang` |
| `--quan ...` | Chỉ lấy tin ở quận/huyện có chứa chữ này, không phân biệt hoa/thường | `--quan "Cầu Giấy" "Đống Đa"` |
| `--gia-min`, `--gia-max` | Khoảng giá thuê, **triệu/tháng** | `--gia-max 30` |
| `--dt-min`, `--dt-max` | Khoảng diện tích, **m²** | `--dt-min 50` |

Ví dụ: văn phòng và mặt bằng ở Cầu Giấy, giá ≤ 30 triệu, diện tích ≥ 50 m², tối đa 100 tin:
```bash
python chotot_scraper.py --loai van-phong mat-bang --quan "Cầu Giấy" --gia-max 30 --dt-min 50 --max-tin 100
```

Lưu ý:
- `--quan`, `--gia-*`, `--dt-*` lọc **sau khi** nhận tin từ API, nên vẫn phải lật hết các trang của nhóm đó. Chỉ `--loai` và `--max-tin` giảm được số request.
- Lần chạy có lọc hoặc giới hạn chỉ lấy **một phần** tin, nên **không đánh dấu** tin nào là "đã gỡ" (`n_removed = NULL`, giao diện hiện "không kiểm tra tin mất"). Muốn cập nhật tin đã gỡ thì cần một lần chạy đầy đủ, không tùy chọn; lịch tự động luôn chạy đầy đủ.
- Xem tất cả tùy chọn: `python chotot_scraper.py --help`.
- Trên giao diện (`python app.py`): mở mục **"Giới hạn lọc / giới hạn cào"** trong ô "Tiến trình cào", điền rồi bấm **Chạy ngay**. Giao diện kiểm tra giá trị rồi gọi đúng các tùy chọn ở trên. Tùy chọn của mỗi lần chạy được lưu trong `chotot_runs.params`.

### Các hàm chính trong `chotot_scraper.py`

| Hàm | Việc làm | Khi nào cần đụng tới |
|---|---|---|
| `main()` | Điểm vào: đọc tham số dòng lệnh, lặp qua `QUERIES`, lật trang, ghi DB, đánh dấu tin đã gỡ, xuất CSV | Chạy qua lệnh `python chotot_scraper.py` |
| `fetch(params)` | Gọi API Chợ Tốt một lần, trả về JSON. Tự thử lại khi gặp 429/5xx | Muốn đổi cách gọi API, header, timeout |
| `row(a, cg)` | Chuyển một tin JSON của Chợ Tốt thành một dòng của bảng (đổi tên trường, đổi mã loại thành nhãn, đổi thời gian) | Muốn thêm/bớt cột dữ liệu |
| `save_listing(db, r, ts, run_id)` | Upsert một tin; so với bản cũ để ghi lịch sử (`moi`, `thay_doi`, `dang_lai`) | Muốn đổi cách ghi lịch sử |
| `changes(old, new)` | So các cột trong `TRACKED`, trả về `{cột: [cũ, mới]}` | |
| `TRACKED` (hằng số) | Các cột được theo dõi thay đổi (giá, diện tích, tiêu đề, mô tả…) | **Muốn theo dõi thêm cột thì thêm vào đây** |
| `db.init_db()` (file `db.py`) | Kết nối PostgreSQL (đọc `DATABASE_URL` từ `.env`) và tạo bảng nếu chưa có | Muốn đổi cấu trúc bảng |
| `log(msg)` | In log ra màn hình và ghi thêm vào `data/scrape.log` | |
| `QUERIES` (hằng số) | Danh sách nhóm tin kéo mặc định | **Muốn đổi loại tin cần lấy thì sửa ở đây** |
| `TYPES` (hằng số) | Bảng tên loại cho `--loai` → truy vấn API tương ứng | Thêm loại mới cho `--loai` |
| `parse_args()` | Khai báo các tùy chọn dòng lệnh (giới hạn cào, giới hạn lọc) | Thêm tùy chọn mới |
| `matches(a, args)` | Tin có khớp `--quan`, `--gia-*`, `--dt-*` không | Thêm điều kiện lọc mới |

Muốn gọi từ code Python khác (ví dụ notebook):

```python
import chotot_scraper as c

data = c.fetch({"region_v2": 12000, "cg": 1030, "st": "u", "limit": 50, "o": 0})
print(data["total"], len(data["ads"]))  # tổng số tin, số tin trong trang này
print(c.row(data["ads"][0], 1030))  # một tin sau khi chuẩn hóa
```

---

## 3. Vì sao chọn Chợ Tốt thay vì batdongsan.com.vn

Đề bài ban đầu yêu cầu batdongsan.com.vn. Đã thử nghiệm ngày 15/11/2026:

| | batdongsan.com.vn | Chợ Tốt Nhà |
|---|---|---|
| Chống bot | **Cloudflare**: `curl`/`requests` nhận 403 ngay từ request đầu | API dữ liệu **không** qua Cloudflare |
| Công cụ cần dùng | Playwright điều khiển Chrome thật, **có hiện cửa sổ** (headless bị kẹt ở trang "Chờ một chút…") | `urllib` (thư viện chuẩn) |
| Ngưỡng bị chặn đo được | ~30 trang trong ~8 phút (4–7 giây/trang) là bị 403 | 36 request liên tiếp (6–12 giây/request) không bị chặn |
| Cách lấy chi tiết | Mở từng trang tin, bóc từ HTML | Có sẵn trong JSON của trang danh sách |
| Số request / lần | ~400 | ~25 |
| Thời gian / lần | 2,5–3 giờ (phải chạy chậm để không bị chặn) | 4–5 phút |

Kết luận: Chợ Tốt cho cùng loại dữ liệu (có tọa độ), nhanh hơn khoảng 30 lần và gần như không có rủi ro bị chặn.

---

## 4. API Chợ Tốt

Khi mở nhatot.com, chính trang web gọi API này để lấy danh sách tin. Script gọi đúng API đó.

```
GET https://gateway.chotot.com/v1/public/ad-listing
```

| Tham số | Giá trị đang dùng | Ý nghĩa |
|---|---|---|
| `region_v2` | `12000` | Hà Nội |
| `cg` | `1020` / `1030` | Danh mục: 1020 = Nhà ở, 1030 = Văn phòng, mặt bằng kinh doanh |
| `st` | `u` | Loại tin: `u` = cho thuê (`s` = bán) |
| `house_type` | `1`, `4` | Loại nhà (chỉ dùng với `cg=1020`) |
| `limit` | `50` | Số tin mỗi trang (tối đa 50) |
| `o` | `0, 50, 100, …` | Offset để lật trang |

Thử ngay trong trình duyệt:
https://gateway.chotot.com/v1/public/ad-listing?region_v2=12000&cg=1030&st=u&limit=2&o=0

**Bảng mã loại** (Chợ Tốt không công bố; tự suy ra bằng cách lọc từng mã rồi đọc tiêu đề tin):

| `house_type` (trong `cg=1020`) | Lấy? | `commercial_type` (trong `cg=1030`) | Lấy? |
|---|---|---|---|
| 1 = Nhà mặt phố, mặt tiền | ✅ | 1 = Shophouse | ✅ |
| 2 = Nhà biệt thự | ❌ (chủ yếu để ở) | 2 = Officetel | ✅ |
| 3 = Nhà ngõ, hẻm | ❌ (chủ yếu để ở) | 3 = Văn phòng | ✅ |
| 4 = Nhà phố liền kề | ✅ | 4 = Mặt bằng kinh doanh | ✅ |

**Kết quả trả về** có dạng `{"total": 533, "ads": [ {...}, {...} ]}`. Các trường dùng trong mỗi tin:

| Trường API | Cột trong DB | Ghi chú |
|---|---|---|
| `list_id` | `list_id` | Mã tin, khóa chính. Link: `https://www.nhatot.com/<list_id>.htm` |
| `subject` | `tieu_de` | |
| `price` / `price_string` | `gia` / `gia_text` | `gia` tính bằng VNĐ/tháng |
| `size` | `dien_tich` | m² |
| `rooms`, `floors`, `toilets` | `so_phong`, `so_tang`, `so_wc` | |
| `street_name`, `ward_name`, `area_name`, `region_name` | `duong`, `phuong`, `quan`, `tinh` | |
| **`latitude`, `longitude`** | **`lat`, `lng`** | **Tọa độ mặt bằng** |
| `body` | `mo_ta` | Mô tả đầy đủ |
| `images` | `anh` | Danh sách link ảnh (JSON) |
| `list_time` | `ngay_dang` | API trả mili giây, script đổi sang ngày giờ |
| (toàn bộ tin) | `raw` | JSON gốc, phòng khi cần trường khác |

---

## 5. Luồng xử lý một lần kéo

```
main()
 ├─ init_db()                          tạo bảng nếu chưa có
 ├─ ghi 1 dòng chotot_runs (status = running)
 ├─ với mỗi (cg, extra) trong QUERIES:
 │    lặp page = 0, 1, 2, …
 │     ├─ fetch(... o = page*50)        gọi API
 │     ├─ với mỗi tin: row() → save_listing(): upsert vào chotot_listings
 │     │     • chưa có trong DB → thêm mới, first_seen = bây giờ  → lịch sử 'moi' (TIN MỚI)
 │     │     • đã có, có cột đổi → cập nhật, last_seen = bây giờ  → lịch sử 'thay_doi' {cột: [cũ, mới]}
 │     │     • trước đó đã gỡ    → bật lại is_active             → lịch sử 'dang_lai'
 │     │     • đã có, không đổi → chỉ cập nhật last_seen
 │     ├─ nghỉ ngẫu nhiên 6–12 giây
 │     └─ dừng khi trang trả về < 50 tin
 ├─ nếu chạy trọn vẹn: tin nào trong DB mà lần này không thấy
 │     → is_active = 0, removed_at = bây giờ → lịch sử 'go' (TIN ĐÃ GỠ)
 ├─ cập nhật chotot_runs: số tin, số mới, số đã gỡ, trạng thái
 └─ xuất lại data/chotot_listings.csv
```

**Theo dõi thay đổi giữa các lần kéo** (yêu cầu "kiểm tra dữ liệu đã mất, dữ liệu thêm mới"):

| Cột | Ý nghĩa |
|---|---|
| `first_seen` | Lần đầu thấy tin này |
| `last_seen` | Lần gần nhất còn thấy tin này |
| `is_active` | 1 = đang đăng, 0 = đã gỡ / hết hạn |
| `removed_at` | Thời điểm phát hiện tin biến mất |

Bảng `chotot_runs` lưu mỗi lần chạy: `started_at`, `finished_at`, `status`, `n_found`, `n_new`, `n_removed`, và `params` (các tùy chọn lọc/giới hạn đã dùng).

**Truy ngược bằng bảng `chotot_history`**: mỗi dòng là một sự kiện của một tin (`moi`, `thay_doi`, `go`, `dang_lai`) kèm thời điểm `at`, lần cào `run_id`, giá, diện tích, tiêu đề, `thay_doi` (cột nào đổi từ gì sang gì) và `raw` (JSON gốc lúc đó). Ví dụ:
```sql
SELECT at, su_kien, gia_text, thay_doi FROM chotot_history WHERE list_id = 135798286 ORDER BY at;
```

Tin chỉ bị đánh dấu "đã gỡ" khi lần chạy **hoàn tất trọn vẹn**. Bước này bị bỏ qua, để không đánh dấu nhầm, khi:
- lần chạy lỗi giữa chừng;
- đang chạy thử hoặc có lọc/giới hạn (`--pages`, `--max-tin`, `--loai`, `--quan`, `--gia-*`, `--dt-*`);
- có nhóm tin lấy được ít hơn 98% số `total` mà API báo (phòng khi API giới hạn số trang được lật). Log khi đó ghi dòng `CẢNH BÁO`.

Khi bước này bị bỏ qua, `chotot_runs.n_removed` = `NULL`. Giao diện chỉ lấy lần chạy có `n_removed` khác `NULL` làm mốc cho bộ lọc "Mới ở lần cào gần nhất".

### Chạy theo lịch

- Đặt lịch trên giao diện (`app.py`, ô "Lịch chạy tự động"): tích các ngày trong tuần hoặc "Hàng ngày", chọn giờ, bấm "Lưu lịch".
- Lịch được lưu vào **Windows Task Scheduler** với tên task `BDS_Chotot_Weekly`, chạy `python chotot_scraper.py`. Task vẫn chạy khi giao diện đang tắt. Nếu đến giờ mà máy tắt, task chạy bù khi bật lại.
- macOS/Linux: dùng cron, ví dụ `0 3 * * * cd /đường/dẫn/Scrape_ESTATES && python3 chotot_scraper.py`.

---

## 6. Phòng tránh bị chặn (rate limit)

- Chạy **một luồng**. Không chạy song song nhiều script hoặc nhiều máy cùng IP.
- Nghỉ ngẫu nhiên giữa các request (mặc định 6–12 giây). Không đặt dưới 5 giây.
- Gửi `User-Agent` của trình duyệt thông thường.
- Gặp HTTP 429 hoặc 5xx: tự nghỉ 2 rồi 4 phút. Lỗi lần thứ 3 thì dừng lần chạy.
- Kéo **tối đa vài lần mỗi ngày**: tin đăng không thay đổi nhanh hơn thế.
- Nếu bị chặn liên tục: dừng, chờ vài giờ, không cố chạy tiếp.

---

## 7. Dự phòng: batdongsan.com.vn (`batdongsan_scraper.py`)

Chỉ dùng khi thật sự cần dữ liệu riêng của batdongsan.

```bash
pip install -r requirements.txt     # có playwright
python batdongsan_scraper.py --limit 3         # chạy thử 3 tin
python batdongsan_scraper.py                   # kéo toàn bộ: 2,5–3 giờ
```

- Các trang danh sách: `cho-thue-nha-mat-pho-tp-ha-noi`, `cho-thue-shophouse-nha-pho-thuong-mai-tp-ha-noi`, `cho-thue-van-phong-tp-ha-noi`. Lật trang bằng `/p2`, `/p3`… (đúng như đề bài: "dùng phần query để tra nhiều trang").
- Bước 1, `collect_links()`: mở từng trang danh sách và lấy link các tin.
- Bước 2: mở từng trang chi tiết và bóc dữ liệu bằng JavaScript (`DETAIL_JS`). **Tọa độ** nằm trong HTML dưới dạng `latitude: …, longitude: …`.
- Dùng Chrome đã cài trên máy (`channel="chrome"`), **phải hiện cửa sổ** và không được đóng cửa sổ khi đang chạy. Cookie được giữ trong `.browser_profile/` (không đưa lên git).
- Nghỉ 15–30 giây giữa các trang, cứ 40 trang nghỉ 2–4 phút. Bị chặn 3 lần liên tiếp thì dừng hẳn.
- Ghi vào bảng `batdongsan_listings` / `batdongsan_runs` trong cùng database PostgreSQL.
