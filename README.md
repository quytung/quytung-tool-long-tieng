# Tool Lồng Tiếng

Ứng dụng Windows dùng OmniVoice trên GPU thuê. Máy cá nhân gửi từng câu và
giọng tham chiếu đến VPS, sau đó ghép audio. Không cần cài model.

## Chạy

- Cài Python 3.11 trở lên rồi mở `CHAY TOOL.bat`.
- Trong PyCharm, chọn interpreter `.venv\Scripts\python.exe` và chạy `main.py`.
- Sau lần clone đầu tiên, dùng Ctrl+T trong cửa sổ project để lấy cập nhật.
  Chạy lại BAT nếu danh sách thư viện thay đổi.

## Sử dụng

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

Cài đặt và cache nằm tại `%LOCALAPPDATA%\ToolLongTieng`. URL và audio cá nhân
không nằm trong thư mục repo. Nội dung và mẫu được gửi tới địa chỉ VPS đã chọn.

## Lịch sử cập nhật

### 0.1.0

- Kết nối OmniVoice VPS qua multipart, nghe thử trực tiếp và xuất WAV/MP3.
- Tốc độ, khoảng nghỉ, chuẩn âm lượng, cắt lặng, phong cách và lời dặn riêng.
- Giới hạn đồng thời, timeout, thử lại và dùng lại câu hoàn thành.
