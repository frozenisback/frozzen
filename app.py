from flask import Flask, request, send_file, jsonify
import asyncio
import aiohttp
import os
import time

app = Flask(__name__)

BOT_TOKEN = "7806439430:AAEIFDC9ez7GWn4ZznMQiXYHVDMlrqCRQ_A"
CHAT_ID = "7634862283"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_BASE_URL = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


async def send_down_command(url: str):
    async with aiohttp.ClientSession() as session:
        await session.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": f"/down {url}"}
        )


async def wait_for_audio_file():
    latest_audio = None
    max_wait = 60  # seconds
    start = time.time()

    async with aiohttp.ClientSession() as session:
        while time.time() - start < max_wait:
            async with session.get(f"{TELEGRAM_API_URL}/getUpdates") as resp:
                updates = await resp.json()

            if "result" in updates:
                for result in reversed(updates["result"]):
                    msg = result.get("message", {})
                    if str(msg.get("chat", {}).get("id")) == CHAT_ID:
                        if "audio" in msg:
                            return msg["audio"]["file_id"]
                        elif "voice" in msg:
                            return msg["voice"]["file_id"]
            await asyncio.sleep(2)

    return None


async def get_file_url(file_id: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{TELEGRAM_API_URL}/getFile", params={"file_id": file_id}) as resp:
            data = await resp.json()
            file_path = data["result"]["file_path"]
            return f"{FILE_BASE_URL}/{file_path}"


async def download_file(url: str, filename: str):
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                with open(filepath, "wb") as f:
                    f.write(await resp.read())
                return filepath
            else:
                return None


@app.route("/down")
def down():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing YouTube URL"}), 400

    async def process():
        await send_down_command(url)
        file_id = await wait_for_audio_file()
        if not file_id:
            return jsonify({"error": "No audio received in time"}), 504

        file_url = await get_file_url(file_id)
        filename = f"{file_id}.m4a"
        filepath = await download_file(file_url, filename)
        if not filepath:
            return jsonify({"error": "Failed to download file"}), 500

        return send_file(filepath, mimetype="audio/m4a", as_attachment=True)

    return asyncio.run(process())


if __name__ == "__main__":
    app.run(port=5000)

