import os, threading, queue, time, re, asyncio
from flask import Flask, request, redirect, jsonify, render_template_string
import nest_asyncio, yt_dlp
from playwright.async_api import async_playwright

nest_asyncio.apply()
ACCESS_CODE = "Itachi123"
app = Flask(__name__)

task_queue = queue.Queue()
bot_logs = []
bot_state = {"status": "Idle", "jazz_number": None, "jazz_otp": None, "waiting_for_otp": False, "cookie_saved": os.path.exists("jazz_cookies.json"), "is_unlocked": False}
target_folder = "ROOT"

def log(msg):
    bot_logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_logs) > 30: bot_logs.pop(0)
    print(msg)

def background_worker():
    global target_folder
    while True:
        try:
            if not task_queue.empty():
                bot_state["status"] = "Working"
                task = task_queue.get()
                link, quality, target_folder = str(task.get('link','')), str(task.get('quality','1080')), str(task.get('folder','ROOT'))
                log(f"> INIT TASK: {link[:30]}... | QUAL: {quality}p")
                file_path, title = download_video(link, quality)
                if file_path:
                    log(f"> DOWNLOAD SUCCESS: {os.path.getsize(file_path)/(1024*1024):.1f} MB")
                    asyncio.run(upload_to_jazzdrive(file_path))
                else: log("> ERROR: DOWNLOAD FAILED.")
                task_queue.task_done()
                bot_state["status"] = "Idle"
        except Exception as e: log(f"> WORKER CRASHED: {e}"); bot_state["status"] = "Idle"
        time.sleep(3)

def download_video(link, quality):
    opts = {'format': f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={quality}][ext=mp4]/best', 'outtmpl': 'downloads/%(title)s.%(ext)s', 'restrictfilenames': True, 'quiet': True, 'no_warnings': True, 'noprogress': True}
    try:
        log(f"> EXECUTING DOWNLOAD ({quality}p)...")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(link, download=True)
            r_path = ydl.prepare_filename(info)
            f_path = re.sub(r'\.\w+$', '.mp4', r_path)
            if not os.path.exists(f_path) and os.path.exists(r_path): f_path = r_path
            return os.path.abspath(f_path), info.get('title', 'video')
    except Exception as e: log(f"> CRITICAL ERROR: {e}"); return None, None

async def upload_to_jazzdrive(file_path):
    if not os.path.exists("jazz_cookies.json"): log("> ERROR: AUTH TOKEN MISSING."); bot_state["cookie_saved"] = False; return False
    async with async_playwright() as p:
        log("> INITIALIZING UPLOAD...")
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(viewport={'width': 1280, 'height': 720}, storage_state="jazz_cookies.json")
        page = await context.new_page()
        try:
            await page.goto("https://cloud.jazzdrive.com.pk/#folders", timeout=90000); await asyncio.sleep(5)
            bot_state["cookie_saved"] = True
            if target_folder.upper() != "ROOT":
                try: await page.get_by_text(target_folder, exact=False).first.click(); await asyncio.sleep(3)
                except: pass
            for sel in ["xpath=/html/body/div/div/div[1]/div/header/div/div/button", "button:has-text('Upload')"]:
                try: await page.click(sel); break
                except: pass
            await page.wait_for_selector("input[type='file']", state="attached")
            async with page.expect_file_chooser() as fc:
                await page.click("xpath=/html/body/div[2]/div[3]/div/div/form/div/div/div/div[1]")
                await fc.value.set_files(file_path)
            empty_checks, last_msg = 0, ""
            log("> DATA TRANSFER IN PROGRESS...")
            for _ in range(1800):
                for ok in ["Uploads completed", "1 file uploaded", "Success"]:
                    if await page.is_visible(f"text={ok}"): log("> TRANSFER COMPLETE."); os.remove(file_path); return True
                try:
                    match = re.search(r"(\d+)%", await page.inner_text("body"))
                    if match and match.group(1)+"%" != last_msg: last_msg = match.group(1)+"%"; log(f"> UPLOADING: {last_msg}"); empty_checks = 0
                    else: empty_checks += 1
                except: empty_checks += 1
                if empty_checks > 15: log("> BACKGROUND TRANSFER..."); empty_checks = 0
                await asyncio.sleep(2)
        except Exception as e: log(f"> UPLOAD ERROR: {e}"); return False
        finally: await browser.close()

async def jazz_login_process(num):
    bot_state["waiting_for_otp"], bot_state["jazz_otp"] = False, None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        page = await context.new_page()
        try:
            log("> INITIATING AUTH..."); await page.goto("https://cloud.jazzdrive.com.pk/login", timeout=90000); await asyncio.sleep(5)
            await page.evaluate(f"() => {{ let ins = document.querySelectorAll('input'); for(let i of ins) {{ if(i.type === 'tel' || (i.placeholder && i.placeholder.includes('03'))) {{ i.value = '{num.strip()}'; i.dispatchEvent(new Event('input', {{ bubbles: true }})); break; }} }} }}")
            await page.keyboard.press('Enter'); await asyncio.sleep(2)
            try: await page.click('#signinbtn', timeout=3000)
            except: await page.evaluate("document.querySelectorAll('button')[0].click()")
            log("> AWAITING OTP (Wait 8s)..."); await asyncio.sleep(8)
            bot_state["waiting_for_otp"] = True; log("> ENTER OTP ON DASHBOARD")
            wait_time = 0
            while bot_state["jazz_otp"] is None:
                await asyncio.sleep(1); wait_time += 1
                if wait_time > 180: log("> AUTH TIMEOUT."); bot_state["waiting_for_otp"] = False; return
            otp = bot_state["jazz_otp"]; log("> VERIFYING OTP...")
            try: await page.fill('#otp', otp.strip(), timeout=2000)
            except:
                for d in otp.strip(): await page.keyboard.press(d); await asyncio.sleep(0.1)
            await asyncio.sleep(1); await page.keyboard.press('Enter'); await asyncio.sleep(2)
            try: await page.click('#signinbtn', timeout=5000)
            except: pass
            log("> GENERATING TOKEN (12s)..."); await asyncio.sleep(12)
            await context.storage_state(path="jazz_cookies.json")
            bot_state["cookie_saved"], bot_state["waiting_for_otp"] = True, False
            log("> SYSTEM ONLINE.")
        except Exception as e: log(f"> AUTH FAILURE: {e}"); bot_state["waiting_for_otp"] = False
        finally: await browser.close()

threading.Thread(target=background_worker, daemon=True).start()

HTML_TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>JazzDrive | Starcore Style</title><script src="https://cdn.tailwindcss.com"></script><style>@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;800&display=swap'); body { background-color: #0d0914; color: white; font-family: 'Poppins', sans-serif; background-image: radial-gradient(circle at 50% 0%, #2e1065 0%, #0d0914 70%); min-height: 100vh;} .glass-card { background: rgba(20, 15, 35, 0.6); backdrop-filter: blur(12px); border-radius: 24px; border: 1px solid rgba(236, 72, 153, 0.2); box-shadow: 0 0 30px rgba(139, 92, 246, 0.15); } .glow-text { background: linear-gradient(90deg, #f472b6, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; } .input-box { background: rgba(0,0,0,0.5); border: 1px solid rgba(255,255,255,0.1); color: white; border-radius: 12px; transition: 0.3s; text-align: center; letter-spacing: 1px;} .input-box:focus { border-color: #c084fc; outline: none; box-shadow: 0 0 15px rgba(192, 132, 252, 0.3); } .btn-primary { background: linear-gradient(90deg, #ec4899, #8b5cf6); color: white; font-weight: 600; border-radius: 9999px; transition: 0.3s; letter-spacing: 1px; cursor: pointer; } .btn-primary:hover { box-shadow: 0 0 20px rgba(236, 72, 153, 0.5); transform: scale(1.02); } .btn-secondary { background: transparent; border: 1px solid #22d3ee; color: #22d3ee; font-weight: 600; border-radius: 9999px; transition: 0.3s; letter-spacing: 1px; cursor: pointer; } .btn-secondary:hover { background: rgba(34, 211, 238, 0.1); box-shadow: 0 0 15px rgba(34, 211, 238, 0.3); } select option { background: #0d0914; color: white; } .terminal { background: rgba(0,0,0,0.6); border-radius: 12px; border: 1px solid rgba(255,255,255,0.05); } ::-webkit-scrollbar { width: 6px; } ::-webkit-scrollbar-track { background: transparent; } ::-webkit-scrollbar-thumb { background: #c084fc; border-radius: 3px; }</style></head><body class="p-4 md:p-8 flex items-center justify-center"><div class="w-full max-w-sm glass-card p-6"><div class="text-center mb-6"><h1 class="text-2xl font-extrabold glow-text tracking-widest flex items-center justify-center gap-2">☁️ JAZZDRIVE PAIR</h1><span class="inline-block mt-2 px-3 py-1 bg-black/50 rounded-full text-[10px] text-green-400 border border-green-400/30 tracking-widest">🔋 SYSTEM ONLINE</span></div><div class="grid grid-cols-2 gap-4 mb-6 text-center text-xs"><div class="bg-black/40 p-2 rounded-xl border border-white/5"><p class="text-gray-500 mb-1">STATUS</p><p id="bot_status" class="font-bold text-pink-400">Loading...</p></div><div class="bg-black/40 p-2 rounded-xl border border-white/5"><p class="text-gray-500 mb-1">QUEUE</p><p id="q_size" class="font-bold text-cyan-400">0</p></div></div><div id="login_section" style="display: none;" class="mb-6"><div class="text-center mb-4"><p class="text-sm text-gray-300">Please Enter Your Jazz Digits</p></div><div id="number_div"><input type="text" id="jazz_num" placeholder="03XXXXXXXXX" class="w-full p-4 mb-4 input-box text-lg"><button onclick="sendNumber()" class="w-full p-4 btn-primary mb-3">🔑 GENERATE CODE</button></div><div id="otp_div" style="display: none;"><input type="text" id="jazz_otp" placeholder="Enter OTP" class="w-full p-4 mb-4 input-box border-pink-500/50 text-lg"><button onclick="sendOTP()" class="w-full p-4 btn-primary mb-3">VERIFY OTP</button></div></div><div id="download_section" class="mb-6"><div class="text-center mb-4"><p class="text-sm text-gray-300">Enter Video Details</p></div><input type="text" id="yt_link" placeholder="YouTube/Direct Link" class="w-full p-3 mb-3 input-box"><input type="text" id="folder_name" placeholder="Folder (ROOT)" class="w-full p-3 mb-3 input-box"><select id="vid_quality" class="w-full p-3 mb-4 input-box"><option value="2160">2160p (4K Ultra HD)</option><option value="1440">1440p (2K Quad HD)</option><option value="1080" selected>1080p (Full HD)</option><option value="720">720p (HD)</option><option value="480">480p (SD)</option><option value="360">360p (Low)</option></select><button onclick="addLink()" class="w-full p-3 btn-primary mb-3">🚀 PLAY NOW</button><button onclick="document.getElementById('yt_link').value=''" class="w-full p-3 btn-secondary">☁ CLEAR</button></div><div class="terminal p-4 h-32 overflow-y-auto text-xs font-mono text-gray-400" id="terminal"><p class="text-pink-400">> Waiting for commands...</p></div></div><script>setInterval(fetchStatus, 2000); function fetchStatus() { fetch('/api/status').then(res => res.json()).then(data => { document.getElementById('bot_status').innerText = data.status; document.getElementById('q_size').innerText = data.queue; if(data.cookie) { document.getElementById('login_section').style.display = 'none'; document.getElementById('download_section').style.display = 'block'; } else { document.getElementById('login_section').style.display = 'block'; document.getElementById('download_section').style.display = 'none'; if(data.waiting_otp) { document.getElementById('number_div').style.display = 'none'; document.getElementById('otp_div').style.display = 'block'; } else { document.getElementById('number_div').style.display = 'block'; document.getElementById('otp_div').style.display = 'none'; } } const term = document.getElementById('terminal'); const isScrolledToBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 10; term.innerHTML = data.logs.map(l => `<p class="mb-1">${l}</p>`).join(''); if(isScrolledToBottom) { term.scrollTop = term.scrollHeight; } }); } function addLink() { const link = document.getElementById('yt_link').value; if(!link) return; fetch('/api/add', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({link: link, folder: document.getElementById('folder_name').value, quality: document.getElementById('vid_quality').value}) }).then(() => { document.getElementById('yt_link').value = ''; }); } function sendNumber() { const num = document.getElementById('jazz_num').value; if(!num) return; fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({number: num}) }); } function sendOTP() { const otp = document.getElementById('jazz_otp').value; if(!otp) return; fetch('/api/otp', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({otp: otp}) }).then(() => { document.getElementById('jazz_otp').value = ''; }); }</script></body></html>"""
LOGIN_TEMPLATE = """<!DOCTYPE html><html><head><title>JAZZDRIVE PAIR | Locked</title><script src="https://cdn.tailwindcss.com"></script><style>@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;800&display=swap'); body { background-color: #0d0914; font-family: 'Poppins', sans-serif; background-image: radial-gradient(circle at 50% 0%, #2e1065 0%, #0d0914 70%); } .glass-card { background: rgba(20, 15, 35, 0.6); backdrop-filter: blur(12px); border-radius: 24px; border: 1px solid rgba(236, 72, 153, 0.2); box-shadow: 0 0 30px rgba(139, 92, 246, 0.15); } .input-box { background: rgba(0,0,0,0.5); border: 1px solid rgba(255,255,255,0.1); color: white; border-radius: 12px; transition: 0.3s; text-align: center; letter-spacing: 3px; } .input-box:focus { border-color: #c084fc; outline: none; box-shadow: 0 0 15px rgba(192, 132, 252, 0.3); } .btn-primary { background: linear-gradient(90deg, #ec4899, #8b5cf6); color: white; font-weight: 600; border-radius: 9999px; transition: 0.3s; letter-spacing: 1px; cursor: pointer; } .btn-primary:hover { box-shadow: 0 0 20px rgba(236, 72, 153, 0.5); transform: scale(1.02); } .glow-text { background: linear-gradient(90deg, #f472b6, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }</style></head><body class="flex items-center justify-center h-screen px-4"><div class="glass-card p-8 w-full max-w-sm text-center"><h2 class="text-3xl font-extrabold mb-2 glow-text tracking-widest">LOCKED</h2><p class="text-gray-400 text-xs mb-8 uppercase tracking-widest">Enter Access Code</p><form method="POST" action="/"><input type="password" name="code" placeholder="••••••" class="w-full p-4 mb-6 input-box text-2xl"><button type="submit" class="w-full p-4 btn-primary">AUTHENTICATE</button></form></div></body></html>"""

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('code', '').lower() == ACCESS_CODE.lower():
            bot_state['is_unlocked'] = True
            log("> SECURITY OVERRIDE ACCEPTED.")
            return redirect('/dashboard')
        return redirect('/')
    if bot_state['is_unlocked']: return redirect('/dashboard')
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/dashboard')
def dashboard():
    if not bot_state['is_unlocked']: return redirect('/')
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/status')
def status():
    if not bot_state['is_unlocked']: return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"status": bot_state["status"], "queue": task_queue.qsize(), "waiting_otp": bot_state["waiting_for_otp"], "cookie": bot_state["cookie_saved"] or os.path.exists("jazz_cookies.json"), "folder": target_folder, "logs": bot_logs})

@app.route('/api/add', methods=['POST'])
def add_task():
    global target_folder
    if not bot_state['is_unlocked']: return "Unauthorized", 401
    data = request.json
    if data.get('folder'): target_folder = data['folder']
    q = data.get('quality', '1080')
    task_queue.put({"link": data['link'], "folder": target_folder, "quality": q})
    log(f"> COMMAND REC. DIR: {target_folder} | {q}p")
    return jsonify({"success": True})

@app.route('/api/login', methods=['POST'])
def api_login():
    if not bot_state['is_unlocked']: return "Unauthorized", 401
    num = request.json.get('number')
    bot_state["jazz_number"] = num
    threading.Thread(target=lambda: asyncio.run(jazz_login_process(num))).start()
    return jsonify({"success": True})

@app.route('/api/otp', methods=['POST'])
def api_otp():
    if not bot_state['is_unlocked']: return "Unauthorized", 401
    bot_state["jazz_otp"] = request.json.get('otp')
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
