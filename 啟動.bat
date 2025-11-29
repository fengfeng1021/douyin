@echo off
:: 切換到檔案所在的目錄 (確保讀得到 ffmpeg 和 cookies.txt)
cd /d "%~dp0"

title Douyin ALAC Converter Server
echo ---------------------------------------------------
echo  Douyin ALAC Converter V17
echo  正在啟動伺服器...
echo  (請勿關閉此視窗，下載完成後直接關閉即可)
echo ---------------------------------------------------

:: 自動打開瀏覽器
start "" "http://127.0.0.1:5000"

:: 啟動 Python 程式
python app.py

pause