# Cào tin cho thuê mặt bằng kinh doanh tại Hà Nội

Công cụ tự động lấy tin **cho thuê để kinh doanh** tại Hà Nội (nhà mặt phố, liền kề, văn phòng, mặt bằng kinh doanh, shophouse, officetel), kèm **tọa độ**, giá, diện tích và mô tả. Dữ liệu lưu vào SQLite, có giao diện web để xem và lịch chạy tự động.

Phương pháp kéo dữ liệu và các hàm cần chạy: xem **[PHUONG_PHAP_KEO_DU_LIEU.md](PHUONG_PHAP_KEO_DU_LIEU.md)**.

## Bắt đầu nhanh

Yêu cầu: Python 3.10 trở lên. Không cần cài thêm thư viện.

```
cd Scrape_ESTATES     # thư mục gốc của repo
python chotot_scraper.py --pages 1    # chạy thử: 50 tin mỗi nhóm, ~30 giây
python chotot_scraper.py              # cào toàn bộ: ~1.200 tin, ~4–5 phút
python app.py                         # giao diện: mở http://127.0.0.1:8000
```

Kết quả nằm trong `data/` (thư mục này không đưa lên git):
- `data/bds.sqlite`: bảng `chotot_listings` (một dòng/tin) và bảng `chotot_runs` (lịch sử các lần chạy).
- `data/chotot_listings.csv`: cùng dữ liệu, mở được bằng Excel hoặc pandas.
- `data/scrape.log`: log của mọi lần chạy.

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
3. Mỗi tin được **upsert** (thêm mới hoặc cập nhật) vào `chotot_listings` theo `list_id`.
4. Theo dõi thay đổi:
   - Tin chưa có trong DB → ghi `first_seen` (đây là **tin mới**).
   - Tin đã có → cập nhật nội dung và `last_seen`.
   - Tin trong DB nhưng lần này không thấy → `is_active = 0`, ghi `removed_at` (**tin đã gỡ**). Bước này chỉ chạy khi lần cào hoàn tất trọn vẹn, để một lần lỗi giữa chừng không đánh dấu nhầm.
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
- Bấm vào một tin để xem chi tiết: **tọa độ** (kèm link Google Maps), bản đồ, ảnh, mô tả, link gốc.
- **Chạy ngay**: cào ngay và xem log trực tiếp. Xong thì bảng tự làm mới.
- **Lịch chạy tự động**: tích các ngày trong tuần (hoặc "Hàng ngày") và chọn giờ. Lịch được lưu vào **Windows Task Scheduler** (task `BDS_Chotot_Weekly`), nên vẫn chạy khi giao diện đang tắt, miễn là đã đăng nhập Windows. Nếu đến giờ mà máy tắt, task sẽ chạy bù khi bật máy.

Phần lịch chỉ hoạt động trên Windows. Trên macOS/Linux, dùng cron để chạy `python chotot_scraper.py`.

---

## Dự phòng: batdongsan.com.vn (`batdongsan_scraper.py`)

Chỉ dùng khi thật sự cần dữ liệu từ batdongsan.

```
pip install -r requirements.txt     # playwright
python batdongsan_scraper.py --limit 3         # chạy thử 3 tin
python batdongsan_scraper.py                   # cào toàn bộ: 2,5–3 giờ
```

- Dùng Chrome đã cài trên máy (`channel="chrome"`) và **phải hiện cửa sổ**: chế độ headless bị kẹt ở trang "Chờ một chút…" của Cloudflare. Không đóng cửa sổ Chrome trong lúc chạy.
- Delay 15–30 giây/trang, cứ 40 trang nghỉ 2–4 phút. Bị chặn 3 lần liên tiếp thì dừng hẳn.
- Tọa độ được bóc từ HTML trang chi tiết (`latitude: …, longitude: …`).
- Ghi vào bảng `listings` (khác bảng `chotot_listings` của Chợ Tốt).
- Nếu IP đã bị chặn thì phải chờ vài giờ, hoặc đổi mạng (ví dụ phát 4G).
