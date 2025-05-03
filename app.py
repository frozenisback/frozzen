import tempfile
from pathlib import Path
import uuid
import hashlib
import glob
import shutil
import random
import time
import requests
from requests.exceptions import ProxyError, RequestException
from flask import Flask, request, jsonify, send_file, abort
import yt_dlp

# =============================================================================
# Configuration & Directories
# =============================================================================

# Use system temp directory (cross-platform)
BASE_TEMP_DIR      = Path(tempfile.gettempdir())
TEMP_DOWNLOAD_DIR  = BASE_TEMP_DIR / "download"
CACHE_DIR          = BASE_TEMP_DIR / "cache"
CACHE_VIDEO_DIR    = BASE_TEMP_DIR / "cache_video"

# Limits & constants
MAX_CACHE_SIZE     = 500 * 1024 * 1024  # 500 MB
COOKIES_FILE       = "cookies.txt"        # Path to cookies (if needed)
SEARCH_API_URL     = "https://odd-block-a945.tenopno.workers.dev/search?title="

# Ensure directories exist
for directory in (TEMP_DOWNLOAD_DIR, CACHE_DIR, CACHE_VIDEO_DIR):
    directory.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Proxy Utilities
# =============================================================================

def get_random_proxy():
    """Fetch a list of HTTP proxies from ProxyScrape and return one at random."""
    try:
        res = requests.get(
            "https://api.proxyscrape.com/v2/?request=displayproxies"
            "&protocol=http&timeout=10000&country=all&anonymity=all",
            timeout=10
        )
        proxies = res.text.strip().splitlines()
        return random.choice(proxies) if proxies else None
    except RequestException:
        return None


def get_working_proxy(max_attempts=5):
    """Select a proxy that successfully connects (HEAD to google.com)."""
    test_url = "https://www.google.com"
    for _ in range(max_attempts):
        proxy = get_random_proxy()
        if not proxy:
            continue
        proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        try:
            requests.head(test_url, proxies=proxies, timeout=5)
            return proxy
        except (ProxyError, RequestException):
            continue
    return None


def proxy_request(url: str, params=None, max_attempts=5, fallback_direct=True):
    """GET with proxy retry; optionally fallback to direct."""
    for _ in range(max_attempts):
        proxy = get_working_proxy()
        kwargs = {"timeout": (10, 120)}  # (connect, read) timeouts in seconds
        if proxy:
            kwargs["proxies"] = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        try:
            return requests.get(url, params=params, **kwargs)
        except (ProxyError, RequestException):
            time.sleep(1)
    if fallback_direct:
        return requests.get(url, params=params, timeout=(10, 120))
    raise RequestException("All proxy attempts failed and direct fallback disabled.")

# =============================================================================
# Cache & Download Helpers
# =============================================================================

def get_cache_key(key_str: str) -> str:
    return hashlib.md5(key_str.encode('utf-8')).hexdigest()


def get_directory_size(directory: Path) -> int:
    total = 0
    for f in directory.rglob('*'):
        if f.is_file():
            total += f.stat().st_size
    return total


def check_cache_size_and_cleanup():
    total = get_directory_size(CACHE_DIR) + get_directory_size(CACHE_VIDEO_DIR)
    if total > MAX_CACHE_SIZE:
        for d in (CACHE_DIR, CACHE_VIDEO_DIR):
            for f in d.iterdir():
                try:
                    f.unlink()
                except Exception:
                    pass


def download_audio(video_url: str) -> str:
    """Download or return cached audio via yt_dlp and rotating proxy."""
    key = get_cache_key(video_url)
    existing = list(CACHE_DIR.glob(f"{key}.*"))
    if existing:
        return str(existing[0])

    proxy = get_working_proxy()
    outtmpl = str(TEMP_DOWNLOAD_DIR / f"{uuid.uuid4()}.%(ext)s")
    ydl_opts = {
        'format': 'worstaudio/worst',
        'outtmpl': outtmpl,
        'noplaylist': True,
        'quiet': True,
        'cookiefile': COOKIES_FILE,
        'socket_timeout': 120,   # increased socket timeout
        'max_memory': 450000,
    }
    if proxy:
        ydl_opts['proxy'] = f"http://{proxy}"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        downloaded_file = ydl.prepare_filename(info)
        ext = info.get('ext', 'm4a')
        dest = CACHE_DIR / f"{key}.{ext}"
        shutil.move(downloaded_file, dest)
        check_cache_size_and_cleanup()
        return str(dest)


def download_video(video_url: str) -> str:
    """Download or return cached video+audio via yt_dlp and rotating proxy."""
    key = get_cache_key(video_url + '_video')
    existing = list(CACHE_VIDEO_DIR.glob(f"{key}.mp4"))
    if existing:
        return str(existing[0])

    proxy = get_working_proxy()
    outtmpl = str(TEMP_DOWNLOAD_DIR / f"{uuid.uuid4()}.%(ext)s")
    ydl_opts = {
        'format': 'bestvideo[height<=144]+worstaudio/worst',
        'outtmpl': outtmpl,
        'noplaylist': True,
        'quiet': True,
        'cookiefile': COOKIES_FILE,
        'merge_output_format': 'mp4',
        'socket_timeout': 120,   # increased socket timeout
        'max_memory': 450000,
    }
    if proxy:
        ydl_opts['proxy'] = f"http://{proxy}"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        downloaded_file = ydl.prepare_filename(info)
        dest = CACHE_VIDEO_DIR / f"{key}.mp4"
        shutil.move(downloaded_file, dest)
        check_cache_size_and_cleanup()
        return str(dest)


def resolve_spotify_link(url: str) -> str:
    """Convert Spotify track URLs to YouTube via external API."""
    if 'spotify.com' not in url:
        return url
    resp = proxy_request(SEARCH_API_URL + url)
    resp.raise_for_status()
    data = resp.json()
    link = data.get('link')
    if not link:
        raise ValueError("Unable to resolve Spotify link to YouTube")
    return link

# =============================================================================
# Flask App & Endpoints
# =============================================================================

app = Flask(__name__)

@app.route('/')
def home():
    return ("<h1>YouTube Downloader API</h1>"
            "<ul>"
            "<li>/search?title=...</li>"
            "<li>/download?url=... or title=...</li>"  
            "<li>/vdown?url=... or title=...</li>"
            "</ul>")

@app.route('/search')
def search_video():
    title = request.args.get('title')
    if not title:
        return jsonify(error="Missing 'title'"), 400
    resp = proxy_request(SEARCH_API_URL + title)
    if resp.status_code != 200:
        return jsonify(error="Search API error"), 500
    data = resp.json()
    link = data.get('link')
    if not link:
        return jsonify(error="No video found"), 404
    return jsonify(title=data.get('title'), url=link, duration=data.get('duration'))

@app.route('/download')
def download_audio_endpoint():
    video_url = request.args.get('url')
    title = request.args.get('title')
    if not video_url and not title:
        return jsonify(error="Require 'url' or 'title'"), 400
    if title and not video_url:
        resp = proxy_request(SEARCH_API_URL + title)
        resp.raise_for_status()
        video_url = resp.json().get('link')
    video_url = resolve_spotify_link(video_url)

    file_path = Path(download_audio(video_url))
    if not file_path.exists():
        abort(404, "Audio file not found")
    return send_file(str(file_path), as_attachment=True, download_name=file_path.name)

@app.route('/vdown')
def download_video_endpoint():
    video_url = request.args.get('url')
    title = request.args.get('title')
    if not video_url and not title:
        return jsonify(error="Require 'url' or 'title'"), 400
    if title and not video_url:
        resp = proxy_request(SEARCH_API_URL + title)
        resp.raise_for_status()
        video_url = resp.json().get('link')
    video_url = resolve_spotify_link(video_url)

    file_path = Path(download_video(video_url))
    if not file_path.exists():
        abort(404, "Video file not found")
    return send_file(str(file_path), as_attachment=True, download_name=file_path.name)

if __name__ == '__main__':
    # For production, use waitress or gunicorn instead of Flask dev server
    from waitress import serve
    serve(
        app,
        host='0.0.0.0',
        port=5000,
        channel_timeout=300  # increase worker timeout to 5 minutes
    )
