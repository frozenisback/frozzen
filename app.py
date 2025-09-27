from flask import Flask, request, jsonify, send_file
import asyncio
import aiohttp
import os
import time
import subprocess
from asyncio.subprocess import DEVNULL

app = Flask(__name__)

# —————— Configuration ——————
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "8481470626:AAH-DFbGT4PRTIVl6SEZNImPV5L8NUhWItU")
CHAT_ID          = os.environ.get("CHAT_ID",   "7634862283")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_BASE_URL    = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR     = os.path.join(BASE_DIR, "downloads")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


async def send_doown_command(url: str):
    async with aiohttp.ClientSession() as session:
        await session.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": f"/down {url}"}
        )


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


async def wait_for_audio_file(timeout: int = 10) -> dict | None:
    """
    Waits for Telegram to send an audio/voice message.
    Returns the message dict if found, else None.
    """
    async with aiohttp.ClientSession() as session:
        await flush_updates(session)
        start = time.time()
        offset = None
        while time.time() - start < timeout:
            params = {"offset": offset} if offset else {}
            resp = await session.get(f"{TELEGRAM_API_URL}/getUpdates", params=params)
            data = await resp.json()
            updates = data.get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                if "audio" in msg or "voice" in msg:
                    return msg  # Return full message to get file_size and duration
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
        return f"{FILE_BASE_URL}/{path}" if path else None


async def download_file_stream(url: str, dest_path: str) -> bool:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                print(f"[ERROR] HTTP {resp.status} for {url}")
                return False
            with open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    f.write(chunk)
            return True


@app.route("/download")
def down():
    yt_url = request.args.get("url")
    if not yt_url:
        return jsonify({"error": "Missing YouTube URL"}), 400

    async def process():
        # Trigger Telegram bot to produce .m4a
        await send_down_command(yt_url)

        # Wait for audio message
        msg = await wait_for_audio_file()
        if not msg:
            return jsonify({"error": "Timeout waiting for audio"}), 504

        # Extract file info
        file_obj = msg.get("audio") or msg.get("voice")
        file_id   = file_obj["file_id"]
        file_size = file_obj.get("file_size", 0)  # bytes
        duration  = file_obj.get("duration", 0)   # seconds

        # Reject large/long songs
        if file_size > 8 * 1024 * 1024 or duration > 600:
            return jsonify({
                "error": "Songs larger than 8 MB or longer than 10 min are not supported. "
                         "Please contact @xyz09723 to upgrade your plan."
            }), 400

        # Get download URL
        download_url = await get_file_url(file_id)
        if not download_url:
            return jsonify({"error": "Failed to get download URL"}), 500

        # Download .m4a
        m4a_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.m4a")
        if not await download_file_stream(download_url, m4a_path):
            return jsonify({"error": "Failed to download .m4a"}), 500

        # Convert to MP3 with fallback
        mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
        try:
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-i", m4a_path,
                "-vn", "-acodec", "libmp3lame", "-b:a", "56k",
                "-threads", "1",
                mp3_path
            ]
            subprocess.run(ffmpeg_cmd, stdout=DEVNULL, stderr=DEVNULL, check=True)
            # Cleanup .m4a after successful conversion
            try:
                os.remove(m4a_path)
            except OSError:
                pass
            return send_file(mp3_path, mimetype="audio/mpeg", as_attachment=True)
        except subprocess.CalledProcessError:
            # Fallback: return the .m4a directly
            return send_file(m4a_path, mimetype="audio/mp4", as_attachment=True)

    return asyncio.run(process())


@app.route("/raw-audio")
def raw_audio():
    spotify_url = request.args.get("url")
    if not spotify_url:
        return jsonify({"error": "Missing Spotify URL"}), 400

    async def process():
        await send_doown_command(spotify_url)

        # Wait for audio message
        msg = await wait_for_audio_file()
        if not msg:
            return jsonify({"error": "Timeout waiting for audio"}), 504

        # Extract file info
        file_obj = msg.get("audio") or msg.get("voice")
        file_id   = file_obj["file_id"]

        # Get download URL
        download_url = await get_file_url(file_id)
        if not download_url:
            return jsonify({"error": "Failed to get download URL"}), 500

        # Download raw audio with .m4a extension
        raw_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.m4a")
        if not await download_file_stream(download_url, raw_path):
            return jsonify({"error": "Failed to download raw audio"}), 500

        return send_file(raw_path, mimetype="audio/mp4", as_attachment=True)

    return asyncio.run(process())



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
