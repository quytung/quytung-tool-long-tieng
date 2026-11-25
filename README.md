# Tool Lồng Tiếng

Ứng dụng Windows dùng Qt (PySide6). Chọn **OmniVoice VPS** để gửi lời đọc và
giọng tham chiếu đến GPU thuê. Tool cũng hỗ trợ Microsoft Edge TTS và các
model cài riêng trên máy.

## Chạy

- Cài Python 3.11 trở lên rồi mở `CHAY TOOL.bat`.
- Trong PyCharm, cài `requirements.txt` vào interpreter của project rồi chạy
  `main.py`. Nếu dùng môi trường do BAT tạo, chọn `.venv\Scripts\python.exe`.
- Sau lần clone đầu tiên, dùng Ctrl+T trong cửa sổ project để lấy cập nhật.
  Chạy lại BAT nếu danh sách thư viện thay đổi.

## Máy đọc

| Máy | Cần gì |
| --- | --- |
| OmniVoice VPS | Địa chỉ máy chủ và giọng mẫu. Có lời dặn nhấn nhá. |
| OmniVoice Local | Cài máy đọc một lần và chọn giọng mẫu. Ưu tiên NVIDIA. |
| Chatterbox Multilingual | Cài máy đọc một lần, chọn mẫu và mã ngôn ngữ engine hỗ trợ. |
| VieNeu-TTS | Cài máy đọc một lần, dùng CPU và mẫu tiếng Việt. |
| Microsoft Edge TTS | Internet và giọng dựng sẵn. |

Các máy đọc cục bộ cài trong runtime riêng, có thể tải vài GB. Tốc độ, khoảng
nghỉ, cắt lặng và chuẩn âm lượng áp dụng cho mọi máy. Lời dặn nhấn nhá ở
bản này chỉ gửi khi chọn OmniVoice VPS.

## Sử dụng OmniVoice VPS

1. Dán URL VPS, tên miền tunnel hoặc IP kèm cổng. Có thể nhập đầy đủ `/v1/tts`.
2. Chọn file giọng mẫu và dán văn bản.
3. Chọn phong cách hoặc nhập lời dặn về nhấn nhá. Mức độ làm theo phụ thuộc
   vào OmniVoice trên VPS và chất lượng mẫu. Tốc độ và khoảng nghỉ được xử lý
   tại máy cậu nên điều chỉnh được cả khi VPS không áp dụng lời dặn.
4. Bấm **Nghe thử qua VPS** để kiểm tra bằng câu đầu và mẫu đang chọn.
5. Bấm **Xuất WAV / MP3** khi hài lòng.

Mặc định gửi hai câu cùng lúc. VPS yếu hoặc hay hết thời gian chờ thì thử
hạ xuống một. Khi lỗi giữa lượt, bấm xuất lại với cùng nội dung, VPS, mẫu
và lời dặn để dùng lại các câu đã tải. Dừng có thể phải chờ các request
đang chạy. Đổi tốc độ hoặc khoảng nghỉ vẫn dùng được cache giọng thô.

## Kho giọng và dữ liệu

Bấm **Kho giọng** cạnh ô giọng mẫu. **Cắt mẫu từ file** cho chọn mốc từ/đến,
giảm nhiễu tùy chọn và nghe đoạn cắt trước khi lưu. **Chép giọng từ LD**
nhận thư mục cài LD hoặc thư mục `voices` có `library.json`. Chọn các giọng cần
chép, rồi chọn một giọng trong kho để dùng. App ưu tiên mẫu LD đã cắt và
có thể tìm kho DubOne/LD tại các thư mục AppData quen thuộc.

Dữ liệu nằm trong `data/` cạnh `main.py`: cài đặt, kho giọng, cache,
model, runtime, file tạm và bản xuất trong `data/output/`. Git bỏ qua
thư mục này. App đọc lại cài đặt từ vị trí cũ nếu chưa có cấu hình mới.
Môi trường Python `.venv` và runtime của máy đọc cục bộ có thể cần
cài lại khi chuyển thư mục hoặc máy. File đầu ra cũ giữ ở vị trí đã xuất.

## Văn bản và SRT

Dán văn bản, kéo thả một file `.srt` vào ô soạn thảo, hoặc bấm **Mở SRT…**.
Mặc định tool chỉ đọc lời thoại theo thứ tự và bỏ số thứ tự cùng thời gian.
Bật **Giữ mốc thời gian SRT** nếu muốn WAV khớp các ô phụ đề. Tool chèn khoảng
lặng, tăng tốc tối đa 2× khi câu dài hơn ô, và báo lỗi nếu ô chồng nhau hoặc
lời vẫn không vừa. Có thể tắt giữ mốc để xuất lời đọc nối tiếp.

## Lịch sử cập nhật

### 0.4.0

- Kho giọng với cắt theo mốc, nghe thử đoạn cắt và giảm nhiễu.
- Chép nhiều giọng từ kho LD/DubOne.
- Gom dữ liệu và đầu ra vào thư mục project.

### 0.3.0

- Nhập SRT bằng kéo thả, chọn file hoặc dán vào trình soạn thảo.
- Đọc nối tiếp hoặc giữ mốc SRT và xuất WAV.

### 0.2.0

- Giao diện Qt tối với cột chọn máy đọc, editor và bảng tùy chỉnh.
- Thêm OmniVoice Local, Chatterbox Multilingual, VieNeu-TTS và Edge TTS.
- Cài riêng từng engine local từ giao diện. Giữ cấu hình VPS đã lưu.

### 0.1.0

- Kết nối OmniVoice VPS qua multipart, nghe thử trực tiếp và xuất WAV/MP3.
- Tốc độ, khoảng nghỉ, chuẩn âm lượng, cắt lặng, phong cách và lời dặn riêng.
- Giới hạn đồng thời, timeout, thử lại và dùng lại câu hoàn thành.
