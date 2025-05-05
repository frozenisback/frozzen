from flask import Flask, request, Response, jsonify, send_file
import asyncio
import aiohttp
import os
import time
import psutil
import threading
import subprocess
from asyncio.subprocess import DEVNULL

app = Flask(__name__)

# —————— Configuration ——————
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "7806439430:AAEIFDC9ez7GWn4ZznMQiXYHVDMlrqCRQ_A")
CHAT_ID          = os.environ.get("CHAT_ID",   "7634862283")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_BASE_URL    = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)



# —————— Telegram Helpers ——————
async def send_down_command(url: str):
    async with aiohttp.ClientSession() as session:
        await session.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": f"/down {url}"}
        )

async def flush_updates(session):
    resp = await session.get(f"{TELEGRAM_API_URL}/getUpdates")
    data = await resp.json()
    if data.get("result"):
        last_id = data["result"][-1]["update_id"]
        await session.get(
            f"{TELEGRAM_API_URL}/getUpdates",
            params={"offset": last_id + 1}
        )

async def wait_for_audio_file(timeout: int = 60) -> str | None:
    async with aiohttp.ClientSession() as session:
        await flush_updates(session)
        start = time.time()
        offset = None
        while time.time() - start < timeout:
            params = {"offset": offset} if offset else {}
            resp = await session.get(f"{TELEGRAM_API_URL}/getUpdates", params=params)
            data    = await resp.json()
            updates = data.get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                if "audio" in msg or "voice" in msg:
                    return (msg.get("audio") or msg.get("voice"))["file_id"]
            await asyncio.sleep(2)
    return None

async def get_file_url(file_id: str) -> str | None:
    async with aiohttp.ClientSession() as session:
        resp = await session.get(
            f"{TELEGRAM_API_URL}/getFile",
            params={"file_id": file_id}
        )
        data = await resp.json()
        if not data.get("ok") or "result" not in data:
            print(f"[ERROR] get_file_url failed: {data}")
            return None
        path = data["result"].get("file_path")
        if not path:
            print(f"[ERROR] get_file_url missing file_path: {data}")
            return None
        return f"{FILE_BASE_URL}/{path}"

async def download_file_stream(url: str, dest_path: str) -> bool:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                print(f"[ERROR] download_file_stream HTTP {resp.status} for {url}")
                return False
            with open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    f.write(chunk)
            return True

# —————— Flask Route ——————
@app.route("/download")
def down():
    yt_url = request.args.get("url")
    if not yt_url:
        return jsonify({"error": "Missing YouTube URL"}), 400

    async def process():
        # 1) Trigger Telegram bot to produce .m4a
        await send_down_command(yt_url)

        # 2) Wait for audio file_id
        file_id = await wait_for_audio_file()
        if not file_id:
            return jsonify({"error": "Timeout waiting for audio"}), 504

        # 3) Download the .m4a
        download_url = await get_file_url(file_id)
        if not download_url:
            return jsonify({"error": "Failed to get download URL"}), 500

        m4a_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.m4a")
        if not await download_file_stream(download_url, m4a_path):
            return jsonify({"error": "Failed to download .m4a"}), 500

        # 4) Convert to MP3 file on disk
        mp3_path = m4a_path.replace(".m4a", ".mp3")
        ffmpeg_cmd = [
            "ffmpeg", "-nostdin",
            "-probesize", "32k", "-analyzeduration", "0",
            "-i", m4a_path,
            "-vn", "-codec:a", "libmp3lame", "-b:a", "56k",
            "-bufsize", "64k", "-rtbufsize", "64k",
            "-indexmem", "32k", "-max_muxing_queue_size", "64",
            "-threads", "1", "-filter_threads", "1",
            mp3_path
        ]
        subprocess.run(ffmpeg_cmd, stdout=DEVNULL, stderr=DEVNULL, check=True)

        # 5) Remove the original .m4a immediately
        try:
            os.remove(m4a_path)
        except OSError:
            pass

        # 6) Return the completed MP3 as one file
        response = send_file(mp3_path, mimetype="audio/mpeg", as_attachment=True)
        @response.call_on_close
        def cleanup_mp3():
            try:
                os.remove(mp3_path)
            except OSError:
                pass

        return response

    return asyncio.run(process())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


