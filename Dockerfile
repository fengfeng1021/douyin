# 使用官方 Python 基礎映像
FROM python:3.9-slim

# 設定工作目錄
WORKDIR /app

# 1. 安裝系統依賴 (FFmpeg 和其他工具)
RUN apt-get update && \
    apt-get install -y ffmpeg wget gnupg && \
    rm -rf /var/lib/apt/lists/*

# 2. 複製檔案到伺服器
COPY . /app

# 3. 安裝 Python 套件
RUN pip install --no-cache-dir -r requirements.txt

# 4. 安裝 Playwright 瀏覽器及其依賴 (最關鍵的一步)
# --with-deps 會自動安裝 Chrome 運行所需的 Linux 系統庫
RUN playwright install --with-deps chromium

# 開放 Port (Render 預設使用 10000)
EXPOSE 10000

# 5. 啟動指令 (使用 Gunicorn 讓伺服器更穩定)
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:10000", "--timeout", "300", "app:app"]