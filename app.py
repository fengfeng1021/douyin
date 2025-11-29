import os
import subprocess
import uuid
import time
import re
import sys
import json
import urllib.request
import urllib.error
import urllib.parse
import threading
from flask import Flask, request, send_file, render_template_string, jsonify, Response, after_this_request
import yt_dlp

# 嘗試匯入 Playwright
try:
    # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

app = Flask(__name__)

# --- 設定區 (V17: 主動觸發版) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'downloads')

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

TASKS = {}

def update_task(job_id, progress, msg, status='processing', filename=None, error=None):
    if job_id in TASKS:
        TASKS[job_id].update({
            'progress': progress,
            'msg': msg,
            'status': status
        })
        if filename: TASKS[job_id]['filename'] = filename
        if error: TASKS[job_id]['error'] = error

def get_ffmpeg_cmd():
    local_ffmpeg = os.path.join(BASE_DIR, 'ffmpeg.exe')
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    return 'ffmpeg'

# 核心功能：真實瀏覽器解析 (主動觸發優化版)
def browser_download(url, output_path, update_callback):
    update_callback(10, "啟動嗅探瀏覽器...")
    if not PLAYWRIGHT_AVAILABLE: raise Exception("未安裝 Playwright")

    title = "video"
    sniffed_urls = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=False, 
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-infobars', '--window-size=400,800']
            )
        except Exception as e:
            raise Exception(f"瀏覽器啟動失敗: {e}")

        context = browser.new_context(
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            viewport={'width': 375, 'height': 812},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            locale='zh-CN'
        )
        
        page = context.new_page()
        
        # --- V17 優化：更寬鬆的流量監聽 ---
        def handle_response(response):
            try:
                ct = response.headers.get('content-type', '').lower()
                u = response.url
                # 只要是 video 類型，或是網址結尾是 mp4，都攔截
                if (('video' in ct or 'mp4' in ct) or '.mp4' in u) and response.status == 200:
                    if u.startswith('http') and '.mp3' not in u and '.m4a' not in u and '.svg' not in u:
                        print(f"[*] 嗅探到媒體流: {u[:50]}...")
                        sniffed_urls.append(u)
            except: pass

        page.on("response", handle_response)

        update_callback(20, "前往頁面並監聽流量...")
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            
            update_callback(35, "模擬操作觸發影片...")
            
            # --- V17 關鍵：主動互動 ---
            try:
                # 1. 模擬點擊螢幕中央 (嘗試觸發播放)
                page.mouse.click(187, 400)
                time.sleep(0.5)
                
                # 2. 模擬滑動 (有些頁面需要捲動才載入)
                page.evaluate("window.scrollTo(0, 300)")
                time.sleep(0.5)
                page.evaluate("window.scrollTo(0, 0)")
                
                # 3. 尋找 video 標籤並強制播放
                video_element = page.query_selector('video')
                if video_element:
                    page.evaluate("document.querySelector('video').play()")
                    print("[*] 已發送 JS 播放指令")
                else:
                    print("[!] 尚未發現 video 標籤，等待載入...")
            except Exception as e:
                print(f"[!] 互動模擬錯誤 (不影響流程): {e}")

            # 等待流量進入
            for _ in range(5):
                if sniffed_urls: break
                time.sleep(1)

            # 嘗試提取標題
            try:
                title_el = page.query_selector('h1') or page.query_selector('.desc')
                if title_el:
                    raw_title = title_el.inner_text()
                    title = "".join([c for c in raw_title if c.isalpha() or c.isdigit() or c==' ' or c in '._-']).strip()
            except: pass
            
            video_src = None
            
            # 策略 A: 使用嗅探到的連結
            if sniffed_urls:
                video_src = sniffed_urls[0]
                print(f"[*] 使用嗅探連結: {video_src[:30]}...")
            
            # 策略 B: 從 DOM 提取
            if not video_src:
                try:
                    video_element = page.query_selector('video')
                    if video_element:
                        video_src = video_element.get_attribute('src')
                except: pass

            if not video_src: 
                # 策略 C: RENDER_DATA
                try:
                    content = page.content()
                    src_match = re.search(r'"src":"(https:[^"]+video[^"]+)"', content)
                    if src_match:
                        video_src = src_match.group(1).replace(r'\u0026', '&')
                except: pass

            if not video_src:
                raise Exception("無法捕捉影片訊號 (嗅探失敗)")
            
            update_callback(60, "下載原始影片流...")
            
            req = urllib.request.Request(video_src, headers={
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
                'Referer': 'https://www.douyin.com/'
            })
            with urllib.request.urlopen(req, timeout=60) as vs, open(output_path, 'wb') as f:
                while True:
                    chunk = vs.read(8192)
                    if not chunk: break
                    f.write(chunk)
                    
        except Exception as e:
            print(f"[!] 瀏覽器操作異常: {e}")
            browser.close()
            raise e
            
        browser.close()
        return title

# 備用功能：雲端矩陣
def cloud_download(url, output_path, update_callback, custom_api=None):
    update_callback(20, "啟動雲端救援模式...")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    providers = []
    
    if custom_api:
        providers.append({'name': '自訂 API', 'url': lambda u: custom_api.strip() + urllib.parse.quote(u), 'parser': lambda d: (d.get('url') or d.get('video', {}).get('noWatermark') or d.get('data', {}).get('url'), d.get('title'))})

    providers.extend([
        {'name': 'TiklyDown', 'url': lambda u: f"https://api.tiklydown.eu.org/api/download?url={urllib.parse.quote(u)}", 'parser': lambda d: (d.get('video', {}).get('noWatermark'), d.get('title'))},
        {'name': 'KuaishouAPI', 'url': lambda u: f"https://api.kuaishouapi.com/douyin/index?url={urllib.parse.quote(u)}", 'parser': lambda d: (d.get('data', {}).get('url') or d.get('data', {}).get('play'), d.get('data', {}).get('desc'))},
        {'name': 'PearkTrue', 'url': lambda u: f"https://api.pearktrue.cn/api/video/douyin/?url={urllib.parse.quote(u)}", 'parser': lambda d: (d.get('data', {}).get('url'), d.get('data', {}).get('title'))}
    ])

    for i, provider in enumerate(providers):
        try:
            update_callback(20 + int((i/len(providers))*30), f"嘗試連線 {provider['name']}...")
            req = urllib.request.Request(provider['url'](url), headers=headers)
            with urllib.request.urlopen(req, timeout=15) as res:
                data = json.loads(res.read().decode())
            v_url, title = provider['parser'](data)
            
            if v_url:
                update_callback(60, f"{provider['name']} 成功！下載中...")
                with urllib.request.urlopen(urllib.request.Request(v_url, headers=headers), timeout=60) as vs, open(output_path, 'wb') as f:
                    while True:
                        chunk = vs.read(8192)
                        if not chunk: break
                        f.write(chunk)
                return title or "video"
        except: continue
    raise Exception("所有雲端線路均失效")

# 背景任務
def process_job_thread(job_id, url, custom_api):
    TASKS[job_id] = {'status': 'processing', 'progress': 5, 'msg': '初始化...'}
    callback = lambda p, m: update_task(job_id, p, m)
    temp_path = os.path.join(DOWNLOAD_FOLDER, f'{job_id}_temp.mp4')
    final_path = os.path.join(DOWNLOAD_FOLDER, f'{job_id}.m4a')
    
    try:
        success = False
        title = "audio"

        if PLAYWRIGHT_AVAILABLE and not custom_api:
            try:
                title = browser_download(url, temp_path, callback)
                success = True
            except Exception as e:
                print(f"[!] Playwright 失敗 (將切換雲端): {e}")
        
        if not success:
            try:
                title = cloud_download(url, temp_path, callback, custom_api)
                success = True
            except Exception as e:
                update_task(job_id, 0, "失敗", status='failed', error=f"解析失敗: {str(e)}")
                return

        if success and os.path.exists(temp_path):
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ' or c in '._-']).strip() or "audio"
            callback(80, "轉碼為 ALAC...")
            try:
                subprocess.run([get_ffmpeg_cmd(), '-y', '-i', temp_path, '-vn', '-acodec', 'alac', final_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try: os.remove(temp_path)
                except: pass
                callback(100, "完成！")
                update_task(job_id, 100, "完成", status='completed', filename=f"{safe_title}.m4a")
            except Exception as e:
                update_task(job_id, 0, "轉檔錯誤", status='failed', error=f"FFmpeg 錯誤: {str(e)}")
        else:
            update_task(job_id, 0, "下載失敗", status='failed', error="無法獲取影片檔案")
    except Exception as e:
        update_task(job_id, 0, "系統錯誤", status='failed', error=str(e))

# HTML Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Douyin ALAC V17</title>
    <style>
        body { background-color: #0a0a0a; color: #e5e5e5; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background: rgba(255,255,255,0.03); padding: 2.5rem; border-radius: 24px; border: 1px solid rgba(255,255,255,0.08); width: 100%; max-width: 480px; text-align: center; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5); backdrop-filter: blur(10px); }
        h1 { font-weight: 300; margin-bottom: 0.5rem; } h1 span { font-weight: 700; color: #fff; }
        input[type="text"] { width: 100%; padding: 16px; background: #151515; border: 1px solid #333; color: #fff; border-radius: 14px; box-sizing: border-box; transition: 0.3s; }
        input:focus { border-color: #666; outline: none; background: #1a1a1a; }
        input.sub-input { background: #111; border: 1px dashed #333; font-size: 0.85rem; margin-top: 10px; display: none; width: 100%; box-sizing: border-box; padding: 10px; color: #ccc; }
        button { width: 100%; padding: 16px; margin-top: 1.5rem; background: #fff; color: #000; border: none; border-radius: 14px; font-weight: 700; cursor: pointer; }
        button:disabled { background: #333; color: #666; }
        .progress-container { margin-top: 2rem; display: none; }
        .progress-bar-bg { width: 100%; height: 6px; background: #222; border-radius: 3px; overflow: hidden; }
        .progress-bar-fill { height: 100%; background: #4ade80; width: 0%; transition: width 0.3s ease; }
        .progress-text { margin-top: 10px; font-size: 0.85rem; color: #888; display: flex; justify-content: space-between; }
        .tip { margin-top: 1.5rem; padding: 12px; border-radius: 10px; font-size: 0.8rem; text-align: left; background: rgba(255,255,255,0.03); display: flex; align-items: center; gap: 8px; }
        .tip.success { border: 1px solid rgba(74, 222, 128, 0.2); color: #4ade80; }
        .tip.warning { border: 1px solid rgba(251, 191, 36, 0.2); color: #fbbf24; }
        details { text-align: left; margin-top: 15px; color: #555; font-size: 0.8rem; cursor: pointer; }
        details[open] input.sub-input { display: block; animation: fadeIn 0.3s; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="container">
        <h1>Douyin <span>ALAC</span></h1>
        <p style="color:#666; font-size:0.8rem; margin-bottom:2rem; font-family:monospace;">HIGH FIDELITY AUDIO CONVERTER V17</p>
        <div id="inputSection">
            <input type="text" id="urlInput" placeholder="貼上抖音連結..." autocomplete="off">
            <details><summary>⚙️ 進階設定</summary><input type="text" id="customApi" class="sub-input" placeholder="自訂 API" autocomplete="off"></details>
            <button onclick="startDownload()" id="submitBtn">下載並轉換</button>
        </div>
        <div class="progress-container" id="progressSection">
            <div class="progress-bar-bg"><div class="progress-bar-fill" id="progressBar"></div></div>
            <div class="progress-text"><span id="progressMsg">初始化中...</span><span id="progressPercent">0%</span></div>
        </div>
        <div id="statusPanel" class="tip">檢查系統...</div>
    </div>
    <script>
        fetch('/check_status').then(r=>r.json()).then(d => {
            const el = document.getElementById('statusPanel');
            el.innerHTML = d.playwright ? "✅ <b>流量嗅探就緒</b>：將自動模擬用戶操作" : "⚠️ <b>未安裝 Playwright</b>：將使用雲端矩陣加速";
            el.className = d.playwright ? 'tip success' : 'tip warning';
        });
        let pollInterval;
        async function startDownload() {
            const url = document.getElementById('urlInput').value;
            const customApi = document.getElementById('customApi').value;
            if(!url) return alert("請輸入網址");
            document.getElementById('submitBtn').disabled = true;
            document.getElementById('inputSection').style.opacity = '0.5';
            document.getElementById('inputSection').style.pointerEvents = 'none';
            document.getElementById('progressSection').style.display = 'block';
            try {
                const res = await fetch('/api/start', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url, custom_api: customApi}) });
                const data = await res.json();
                if(data.job_id) pollProgress(data.job_id);
                else throw new Error("任務建立失敗");
            } catch(e) { alert("錯誤: " + e.message); resetUI(); }
        }
        function pollProgress(jobId) {
            pollInterval = setInterval(async () => {
                try {
                    const res = await fetch('/api/progress/' + jobId);
                    const data = await res.json();
                    document.getElementById('progressBar').style.width = data.progress + '%';
                    document.getElementById('progressMsg').innerText = data.msg;
                    document.getElementById('progressPercent').innerText = data.progress + '%';
                    if (data.status === 'completed') {
                        clearInterval(pollInterval);
                        document.getElementById('progressBar').style.background = '#4ade80';
                        document.getElementById('progressMsg').innerText = "下載開始！";
                        window.location.href = '/api/get_file/' + jobId;
                        setTimeout(resetUI, 3000);
                    } else if (data.status === 'failed') {
                        clearInterval(pollInterval);
                        alert("失敗: " + data.error);
                        resetUI();
                    }
                } catch(e) { console.error(e); }
            }, 1000);
        }
        function resetUI() {
            document.getElementById('submitBtn').disabled = false;
            document.getElementById('inputSection').style.opacity = '1';
            document.getElementById('inputSection').style.pointerEvents = 'auto';
            setTimeout(() => { document.getElementById('progressSection').style.display = 'none'; document.getElementById('progressBar').style.width = '0%'; }, 2000);
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/check_status')
def check_status(): return {"playwright": PLAYWRIGHT_AVAILABLE}

@app.route('/api/start', methods=['POST'])
def api_start():
    data = request.json
    url = data.get('url', '')
    match = re.search(r'(https?://[a-zA-Z0-9./?=&_\-%]+)', url)
    if not match: return jsonify({'error': 'Invalid URL'}), 400
    job_id = str(uuid.uuid4())
    threading.Thread(target=process_job_thread, args=(job_id, match.group(1), data.get('custom_api'))).start()
    return jsonify({'job_id': job_id})

@app.route('/api/progress/<job_id>')
def api_progress(job_id):
    task = TASKS.get(job_id)
    if not task: return jsonify({'error': 'Not found'}), 404
    return jsonify(task)

@app.route('/api/get_file/<job_id>')
def api_get_file(job_id):
    task = TASKS.get(job_id)
    if not task or task['status'] != 'completed': return "File not ready", 404
    
    final_path = os.path.join(DOWNLOAD_FOLDER, f'{job_id}.m4a')
    # 解決中文檔名問題：對檔名進行 URL 編碼
    filename = urllib.parse.quote(task.get('filename', 'audio.m4a'))
    
    if not os.path.exists(final_path): return "File missing", 404

    @after_this_request
    def remove_file(response):
        def delayed_delete():
            time.sleep(10)
            try: os.remove(final_path)
            except: pass
            TASKS.pop(job_id, None)
        threading.Thread(target=delayed_delete).start()
        
        response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
        return response
        
    try:
        return send_file(final_path, mimetype='audio/mp4')
    except Exception as e:
        print(f"[!] 傳輸錯誤: {e}")
        return f"Download Error: {e}", 500

if __name__ == '__main__':
    print("[*] 伺服器啟動: http://127.0.0.1:5000")
    if not PLAYWRIGHT_AVAILABLE: print("[!] 提示：建議執行 pip install playwright && playwright install chromium")
    app.run(host='0.0.0.0', port=5000, debug=False)