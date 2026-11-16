# Cào tin cho thuê mặt bằng kinh doanh tại Hà Nội

Công cụ tự động lấy tin **cho thuê để kinh doanh** tại Hà Nội (nhà mặt phố, liền kề, văn phòng, mặt bằng kinh doanh, shophouse, officetel), kèm **tọa độ**, giá, diện tích và mô tả. Dữ liệu lưu vào **PostgreSQL**, có **lịch sử thay đổi** của từng tin để truy ngược, giao diện web để xem và lịch chạy tự động.

Phương pháp kéo dữ liệu và các hàm cần chạy: xem **[PHUONG_PHAP_KEO_DU_LIEU.md](PHUONG_PHAP_KEO_DU_LIEU.md)**.

## Cài đặt

Yêu cầu: Python 3.10 trở lên, PostgreSQL (máy đang dùng PostgreSQL 16, cổng **5433**).

**1. Cài thư viện**
```
pip install -r requirements.txt
```

**2. Tạo user và database PostgreSQL** (một lần; mở psql bằng tài khoản `postgres`):
```
"C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5433 -U postgres
```
```sql
CREATE ROLE scraper LOGIN PASSWORD 'mật-khẩu-của-bạn';
CREATE DATABASE bds OWNER scraper;        -- dữ liệu thật
CREATE DATABASE bds_test OWNER scraper;   -- (không bắt buộc) chỉ dùng khi chạy bộ test
```

**3. Tạo file `.env`** cạnh code (chép từ `.env.example`, thay mật khẩu). File này nằm trong `.gitignore`, **không bao giờ** đưa lên git:
```
DATABASE_URL=postgresql://scraper:mật-khẩu-của-bạn@localhost:5433/bds
TEST_DATABASE_URL=postgresql://scraper:mật-khẩu-của-bạn@localhost:5433/bds_test
```
Bảng được tạo tự động ở lần chạy đầu tiên.

**4. (Chỉ khi có dữ liệu SQLite cũ)** chuyển sang PostgreSQL, chạy một lần (chạy lại cũng không bị trùng):
```
python migrate_sqlite_to_pg.py
```

## Bắt đầu nhanh

```
cd Scrape_ESTATES     # thư mục gốc của repo
python chotot_scraper.py --pages 1    # chạy thử: 50 tin mỗi nhóm, ~30 giây
python chotot_scraper.py              # cào toàn bộ: ~1.200 tin, ~4–5 phút
python chotot_scraper.py --loai van-phong --quan "Cầu Giấy" --gia-max 30 --max-tin 100   # có lọc + giới hạn
python app.py                         # giao diện: mở http://127.0.0.1:8000
```

Dữ liệu nằm trong PostgreSQL (database `bds`):
| Bảng | Nội dung |
|---|---|
| `chotot_listings` | Trạng thái **mới nhất** của mỗi tin (một dòng/tin), có `first_seen`, `last_seen`, `is_active`, `removed_at` |
| `chotot_history` | **Lịch sử**: mỗi khi tin xuất hiện (`moi`), thay đổi (`thay_doi`, kèm cột nào đổi từ gì sang gì), bị gỡ (`go`) hoặc đăng lại (`dang_lai`) |
| `chotot_runs` | Mỗi lần cào: thời gian, kết quả, số tin mới/gỡ, **tùy chọn đã dùng** (`params`) |
| `batdongsan_listings`, `batdongsan_runs` | Của bản dự phòng batdongsan |

Ngoài ra thư mục `data/` (không đưa lên git) có `chotot_listings.csv` (bản xuất để mở bằng Excel) và `scrape.log`.

Ví dụ truy ngược bằng SQL:
```sql
-- Tin 135798286 đã thay đổi những gì, khi nào?
SELECT at, su_kien, gia_text, thay_doi FROM chotot_history WHERE list_id = 135798286 ORDER BY at;
-- Giá của mọi tin văn phòng tại ngày 20/11/2026 (bản ghi gần nhất trước ngày đó)
SELECT DISTINCT ON (h.list_id) h.list_id, h.gia, h.at
FROM chotot_history h JOIN chotot_listings l USING (list_id)
WHERE l.loai = 'Văn phòng' AND h.at < '2026-11-21' AND h.su_kien <> 'go'
ORDER BY h.list_id, h.at DESC;
```

---

## Cách cào hoạt động

### 1. Vì sao dùng Chợ Tốt Nhà chứ không phải batdongsan.com.vn

Đã thử cả hai trang (15/11/2026):

| | batdongsan.com.vn | Chợ Tốt Nhà (nhatot.com) |
|---|---|---|
| Chống bot | Cloudflare: request thường bị 403 ngay từ đầu | API dữ liệu không có Cloudflare |
| Cách lấy được | Phải điều khiển Chrome thật, có hiện cửa sổ | Gọi HTTP bình thường, nhận JSON |
| Bị chặn khi nào | Khoảng 30 trang trong 8 phút là bị 403 | 36 request (6–12 giây/request) chưa bị chặn |
| Số request / lần cào | ~400 (danh sách + từng trang chi tiết) | ~25 (danh sách đã có đủ chi tiết) |
| Thời gian / lần cào | 2,5–3 giờ | 4–5 phút |
| Tọa độ | Có, phải bóc từ HTML trang chi tiết | Có sẵn trong JSON (`latitude`, `longitude`) |

### 2. API của Chợ Tốt

Khi mở nhatot.com, trang web tự gọi một API JSON công khai để lấy danh sách tin. Script gọi đúng API đó:

```
https://gateway.chotot.com/v1/public/ad-listing
    ?region_v2=12000      # Hà Nội
    &cg=1030              # danh mục (bảng dưới)
    &st=u                 # u = cho thuê
    &house_type=1         # loại nhà (chỉ dùng với cg=1020)
    &limit=50             # số tin mỗi trang (tối đa 50)
    &o=0                  # offset: 0, 50, 100, ... để lật trang
```

Mở thử link này trong trình duyệt để xem JSON trả về:
https://gateway.chotot.com/v1/public/ad-listing?region_v2=12000&cg=1030&st=u&limit=2&o=0

Các mã đang dùng:

| Tham số | Mã | Ý nghĩa | Có lấy? |
|---|---|---|---|
| `cg` | 1020 | Nhà ở | Chỉ mặt phố + liền kề |
| `cg` | 1030 | Văn phòng, mặt bằng kinh doanh | Lấy hết |
| `house_type` | 1 / 2 / 3 / 4 | Mặt phố / Biệt thự / Ngõ, hẻm / Liền kề | 1 và 4 |
| `commercial_type` | 1 / 2 / 3 / 4 | Shophouse / Officetel / Văn phòng / Mặt bằng KD | Tất cả |

Chợ Tốt không công bố bảng mã `house_type` và `commercial_type`. Nhãn ở trên được suy ra bằng cách lọc theo từng mã rồi đọc tiêu đề tin. Muốn đổi loại cần lấy, sửa `QUERIES` trong `chotot_scraper.py`.

Một số trường quan trọng trong mỗi tin (`ads[i]`): `list_id` (mã tin), `subject`, `price`, `price_string`, `size`, `rooms`, `floors`, `toilets`, `street_name`, `ward_name`, `area_name`, **`latitude`, `longitude`**, `body` (mô tả), `images`, `list_time` (mili giây). Link xem tin: `https://www.nhatot.com/<list_id>.htm`.

### 3. Luồng chạy của `chotot_scraper.py`

1. Với từng truy vấn trong `QUERIES`, gọi API với `o = 0, 50, 100, …` cho tới khi một trang trả về ít hơn 50 tin.
2. Giữa hai request nghỉ **ngẫu nhiên 6–12 giây**. Nếu gặp HTTP 429 hoặc 5xx thì nghỉ 2–4 phút rồi thử lại.
3. Mỗi tin được **upsert** (thêm mới hoặc cập nhật) vào `chotot_listings` theo `list_id`. Trước khi ghi, so với bản cũ: tin mới, đổi giá/diện tích/tiêu đề/mô tả…, hoặc đăng lại thì ghi thêm một dòng vào `chotot_history`.
4. Theo dõi thay đổi:
   - Tin chưa có trong DB → ghi `first_seen` (đây là **tin mới**).
   - Tin đã có → cập nhật nội dung và `last_seen`.
   - Tin trong DB nhưng lần này không thấy → `is_active = 0`, ghi `removed_at` và một dòng `go` vào lịch sử (**tin đã gỡ**). Bước này chỉ chạy khi lần cào hoàn tất trọn vẹn và không có lọc/giới hạn, để không đánh dấu nhầm.
5. Ghi một dòng vào `chotot_runs` (số tin, số tin mới, số tin đã gỡ) và xuất lại CSV.

### 4. Lưu ý để không bị chặn

- Chạy **một luồng**, không chạy song song nhiều script.
- Không giảm delay dưới 5 giây. Với ~25 request mỗi lần thì cũng không cần nhanh hơn.
- Cào **tối đa vài lần mỗi ngày**. Dữ liệu tin đăng không thay đổi nhanh hơn thế.
- Nếu gặp 403 hoặc 429 liên tục: dừng lại, chờ vài giờ rồi mới thử lại.

---

## Giao diện (`app.py`)

`python app.py` rồi mở http://127.0.0.1:8000:
- Thống kê: tổng trong DB, đang đăng, người đăng đã gỡ.
- Lọc theo từ khóa, loại, quận, khoảng giá, trạng thái (đang đăng / mới ở lần cào gần nhất / đã gỡ). Có bản đồ tổng quan.
- Bấm vào một tin để xem chi tiết: **tọa độ** (kèm link Google Maps), bản đồ, ảnh, mô tả, link gốc, và bảng **Lịch sử thay đổi**.
- **Chạy ngay**: cào ngay và xem log trực tiếp. Xong thì bảng tự làm mới. Mở mục **"Giới hạn lọc / giới hạn cào"** để chọn loại, quận, khoảng giá, khoảng diện tích, số tin tối đa cho lần chạy đó (để trống = cào hết).
- **Lịch chạy tự động**: tích các ngày trong tuần (hoặc "Hàng ngày") và chọn giờ. Lịch được lưu vào **Windows Task Scheduler** (task `BDS_Chotot_Weekly`), nên vẫn chạy khi giao diện đang tắt, miễn là đã đăng nhập Windows. Nếu đến giờ mà máy tắt, task sẽ chạy bù khi bật máy.

Phần lịch chỉ hoạt động trên Windows. Trên macOS/Linux, dùng cron để chạy `python chotot_scraper.py`.

---

## Dự phòng: batdongsan.com.vn (`batdongsan_scraper.py`)

Chỉ dùng khi thật sự cần dữ liệu từ batdongsan.

```
pip install -r requirements.txt     # có playwright
python batdongsan_scraper.py --limit 3         # chạy thử 3 tin
python batdongsan_scraper.py                   # cào toàn bộ: 2,5–3 giờ
```

- Dùng Chrome đã cài trên máy (`channel="chrome"`) và **phải hiện cửa sổ**: chế độ headless bị kẹt ở trang "Chờ một chút…" của Cloudflare. Không đóng cửa sổ Chrome trong lúc chạy.
- Delay 15–30 giây/trang, cứ 40 trang nghỉ 2–4 phút. Bị chặn 3 lần liên tiếp thì dừng hẳn.
- Tọa độ được bóc từ HTML trang chi tiết (`latitude: …, longitude: …`).
- Ghi vào bảng `batdongsan_listings` / `batdongsan_runs` trong cùng database PostgreSQL.
- Nếu IP đã bị chặn thì phải chờ vài giờ, hoặc đổi mạng (ví dụ phát 4G).
