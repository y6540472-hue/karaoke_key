import os
import re
import json
import time
import tempfile
import subprocess
import base64
import secrets
import numpy as np
import librosa
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from contextlib import asynccontextmanager

COOKIES_PATH = "/tmp/yt_cookies.txt"

# --- 環境変数 ---
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")


def restore_cookies():
    """環境変数からcookies.txtを復元する"""
    b64 = os.environ.get("YT_COOKIES_B64")
    if b64:
        with open(COOKIES_PATH, "w") as f:
            f.write(base64.b64decode(b64).decode("utf-8"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    restore_cookies()
    yield


app = FastAPI(title="Karaoke Key Analyzer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


import shutil

YT_DLP_PATH = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"


def get_env():
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


# ============================================================
# YouTube Data API v3
# ============================================================

def parse_iso_duration(iso: str) -> Optional[str]:
    """ISO 8601 duration (PT3M45S) を MM:SS に変換"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return None
    hours = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    total_mins = hours * 60 + mins
    return f"{total_mins}:{secs:02d}"


def search_youtube_api(query: str, count: int) -> list:
    """YouTube Data API v3 で動画検索"""
    count = min(count, 50)
    search_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": count,
            "key": YOUTUBE_API_KEY,
        },
        timeout=10,
    )
    search_resp.raise_for_status()
    items = search_resp.json().get("items", [])
    video_ids = [item["id"]["videoId"] for item in items]

    # 再生時間を一括取得
    duration_map = {}
    if video_ids:
        v_resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "contentDetails",
                "id": ",".join(video_ids),
                "key": YOUTUBE_API_KEY,
            },
            timeout=10,
        )
        v_resp.raise_for_status()
        for v in v_resp.json().get("items", []):
            duration_map[v["id"]] = parse_iso_duration(
                v["contentDetails"]["duration"]
            )

    results = []
    for item in items:
        vid_id = item["id"]["videoId"]
        snippet = item["snippet"]
        thumbs = snippet.get("thumbnails", {})
        thumb = (
            (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {})
            .get("url", f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg")
        )
        results.append({
            "id": vid_id,
            "title": snippet.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={vid_id}",
            "thumbnail": thumb,
            "channel": snippet.get("channelTitle", ""),
            "duration": duration_map.get(vid_id),
        })
    return results


def get_video_title_from_api(video_id: str) -> Optional[str]:
    """YouTube Data API v3 でビデオタイトルを取得"""
    if not YOUTUBE_API_KEY:
        return None
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet", "id": video_id, "key": YOUTUBE_API_KEY},
        timeout=10,
    )
    if resp.status_code != 200:
        return None
    items = resp.json().get("items", [])
    if not items:
        return None
    return items[0]["snippet"].get("title")


def extract_video_id(url: str) -> Optional[str]:
    """YouTube URL から video ID を抽出"""
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


# ============================================================
# Spotify Audio Features API
# ============================================================

_spotify_token: dict = {}


def get_spotify_token() -> str:
    """Spotify Client Credentials フローでアクセストークン取得"""
    now = time.time()
    if _spotify_token.get("expires_at", 0) > now + 60:
        return _spotify_token["access_token"]

    credentials = base64.b64encode(
        f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {credentials}"},
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _spotify_token["access_token"] = data["access_token"]
    _spotify_token["expires_at"] = now + data["expires_in"]
    return _spotify_token["access_token"]


def get_key_from_spotify(title: str) -> Optional["KeyResult"]:
    """Spotify Audio Features API で曲のキーを取得（音声ダウンロード不要）"""
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None

    try:
        token = get_spotify_token()
        headers = {"Authorization": f"Bearer {token}"}

        # 曲を検索
        search_resp = requests.get(
            "https://api.spotify.com/v1/search",
            headers=headers,
            params={"q": title, "type": "track", "limit": 1},
            timeout=10,
        )
        search_resp.raise_for_status()
        tracks = search_resp.json().get("tracks", {}).get("items", [])
        if not tracks:
            return None

        track_id = tracks[0]["id"]

        # Audio Features でキーを取得
        feat_resp = requests.get(
            f"https://api.spotify.com/v1/audio-features/{track_id}",
            headers=headers,
            timeout=10,
        )
        feat_resp.raise_for_status()
        feat = feat_resp.json()

        key_idx = feat.get("key", -1)
        mode = feat.get("mode", 1)  # 1=major, 0=minor

        if key_idx == -1:
            return None

        mode_str = "major" if mode == 1 else "minor"
        mode_ja = "メジャー" if mode == 1 else "マイナー"

        return KeyResult(
            key=f"{KEY_NAMES[key_idx]} {mode_ja}",
            key_index=key_idx,
            mode=mode_str,
            confidence=0.9,  # Spotify公式データ
        )
    except Exception:
        return None


# ============================================================
# yt-dlp（フォールバック用）
# ============================================================

def download_youtube_audio(url: str, output_path: str) -> None:
    """YouTubeから音声をダウンロードする（複数クライアントでリトライ）"""
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    base_args = [
        YT_DLP_PATH,
        "-x",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "-o", output_path,
        "--no-playlist",
        "--user-agent", user_agent,
    ]

    if os.path.exists(COOKIES_PATH):
        base_args += ["--cookies", COOKIES_PATH]

    client_options = [
        ["--extractor-args", "youtube:player_client=ios"],
        ["--extractor-args", "youtube:player_client=tv_embedded"],
        ["--extractor-args", "youtube:player_client=mweb"],
        ["--extractor-args", "youtube:player_client=web"],
        [],
    ]

    last_error = ""
    for extra_args in client_options:
        try:
            result = subprocess.run(
                base_args + extra_args + [url],
                capture_output=True,
                text=True,
                timeout=120,
                env=get_env(),
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="yt-dlpが見つかりません")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=408, detail="タイムアウトしました")

        if result.returncode == 0:
            return
        last_error = (result.stderr or "") + (result.stdout or "")

        import glob
        dir_path = os.path.dirname(output_path)
        for f in glob.glob(os.path.join(dir_path, "*")):
            os.remove(f)

    raise HTTPException(
        status_code=400,
        detail=f"YouTube音声の取得に失敗しました: {last_error[:300]}",
    )


def search_youtube_ytdlp(query: str, count: int) -> list:
    """yt-dlp で YouTube 検索（フォールバック）"""
    count = min(count, 30)
    result = subprocess.run(
        [
            YT_DLP_PATH,
            f"ytsearch{count}:{query}",
            "--dump-json",
            "--no-download",
            "--flat-playlist",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=get_env(),
    )

    results = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)
            video_id = data.get("id", "")
            duration_secs = data.get("duration")
            duration_str = None
            if duration_secs:
                mins, secs = divmod(int(duration_secs), 60)
                duration_str = f"{mins}:{secs:02d}"
            results.append({
                "id": video_id,
                "title": data.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": data.get("thumbnails", [{}])[-1].get("url", "")
                    if data.get("thumbnails")
                    else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "channel": data.get("channel", data.get("uploader", "")),
                "duration": duration_str,
            })
        except json.JSONDecodeError:
            continue
    return results


# ============================================================
# モデル定義
# ============================================================

class YouTubeRequest(BaseModel):
    url: str


class SearchRequest(BaseModel):
    query: str


class SearchResult(BaseModel):
    id: str
    title: str
    url: str
    thumbnail: str
    channel: str
    duration: Optional[str] = None


class KeyResult(BaseModel):
    key: str
    key_index: int
    mode: str  # "major" or "minor"
    confidence: float


class KeyDiffResult(BaseModel):
    original: KeyResult
    user: KeyResult
    semitone_diff: int
    recommendation: str


# ============================================================
# キー検出（音声ファイル）
# ============================================================

def detect_key(audio_path: str) -> KeyResult:
    """音声ファイルからキー（調）を検出する"""
    y, sr = librosa.load(audio_path, sr=22050, duration=60)

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)

    major_profile = np.array(
        [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
    )
    minor_profile = np.array(
        [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
    )

    major_correlations = []
    minor_correlations = []

    for i in range(12):
        shifted = np.roll(chroma_mean, -i)
        major_corr = np.corrcoef(shifted, major_profile)[0, 1]
        minor_corr = np.corrcoef(shifted, minor_profile)[0, 1]
        major_correlations.append(major_corr)
        minor_correlations.append(minor_corr)

    best_major_idx = int(np.argmax(major_correlations))
    best_minor_idx = int(np.argmax(minor_correlations))
    best_major_corr = major_correlations[best_major_idx]
    best_minor_corr = minor_correlations[best_minor_idx]

    if best_major_corr >= best_minor_corr:
        return KeyResult(
            key=f"{KEY_NAMES[best_major_idx]} メジャー",
            key_index=best_major_idx,
            mode="major",
            confidence=round(float(best_major_corr), 3),
        )
    else:
        return KeyResult(
            key=f"{KEY_NAMES[best_minor_idx]} マイナー",
            key_index=best_minor_idx,
            mode="minor",
            confidence=round(float(best_minor_corr), 3),
        )


def calculate_semitone_diff(original: KeyResult, user: KeyResult) -> int:
    """2つのキーの半音差を計算（-6 〜 +6 の範囲）"""
    diff = user.key_index - original.key_index
    if diff > 6:
        diff -= 12
    elif diff < -6:
        diff += 12
    return diff


# ============================================================
# API エンドポイント
# ============================================================

@app.get("/api/search")
async def search_youtube(q: str, count: int = 10):
    """YouTube動画を検索する（YouTube API優先、yt-dlpフォールバック）"""
    try:
        if YOUTUBE_API_KEY:
            raw = search_youtube_api(q, count)
        else:
            raw = search_youtube_ytdlp(q, count)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="検索がタイムアウトしました")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"検索APIエラー: {str(e)[:200]}")

    return [SearchResult(**r) for r in raw]


@app.post("/api/analyze-youtube", response_model=KeyResult)
async def analyze_youtube(request: YouTubeRequest):
    """YouTubeリンクから曲のキーを解析する（Spotify優先、yt-dlpフォールバック）"""

    # 1. Spotify でキーを取得（音声ダウンロード不要）
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        video_id = extract_video_id(request.url)
        title = None

        # YouTube API でタイトル取得
        if video_id and YOUTUBE_API_KEY:
            title = get_video_title_from_api(video_id)

        # YouTube API 未設定なら yt-dlp でタイトル取得（メタデータのみ、軽量）
        if not title and video_id:
            try:
                result = subprocess.run(
                    [YT_DLP_PATH, "--dump-json", "--no-download", request.url],
                    capture_output=True, text=True, timeout=30, env=get_env(),
                )
                if result.returncode == 0:
                    title = json.loads(result.stdout).get("title")
            except Exception:
                pass

        if title:
            spotify_result = get_key_from_spotify(title)
            if spotify_result:
                return spotify_result

    # 2. yt-dlp フォールバック（音声ダウンロード）
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "audio.wav")
        download_youtube_audio(request.url, output_path)

        actual_files = [f for f in os.listdir(tmpdir)]
        if not actual_files:
            raise HTTPException(status_code=500, detail="音声ファイルが見つかりません")

        actual_path = os.path.join(tmpdir, actual_files[0])
        return detect_key(actual_path)


@app.post("/api/analyze-voice", response_model=KeyResult)
async def analyze_voice(file: UploadFile = File(...)):
    """マイク録音した音声からキーを解析する"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = detect_key(tmp_path)
        return result
    finally:
        os.unlink(tmp_path)


@app.post("/api/compare-keys", response_model=KeyDiffResult)
async def compare_keys(original: KeyResult, user: KeyResult):
    """原曲と自分のキーを比較して、カラオケ設定を提案する"""
    diff = calculate_semitone_diff(original, user)

    if diff == 0:
        recommendation = "原曲キーのままでOK！そのまま歌えます。"
    elif diff > 0:
        recommendation = f"カラオケで +{diff} に設定しましょう。"
    else:
        recommendation = f"カラオケで {diff} に設定しましょう。"

    return KeyDiffResult(
        original=original,
        user=user,
        semitone_diff=diff,
        recommendation=recommendation,
    )


@app.get("/api/health")
async def health():
    cookie_status = "loaded" if os.path.exists(COOKIES_PATH) else "not_set"
    return {
        "status": "ok",
        "cookies": cookie_status,
        "youtube_api": "enabled" if YOUTUBE_API_KEY else "disabled (yt-dlp fallback)",
        "spotify_api": "enabled" if (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET) else "disabled (yt-dlp fallback)",
    }


class CookiesUpdateRequest(BaseModel):
    cookies_b64: str


@app.post("/api/admin/cookies")
async def update_cookies(
    request: CookiesUpdateRequest,
    authorization: Optional[str] = Header(None),
):
    """Cookie認証ファイルを更新する（要: ADMIN_SECRETヘッダー）"""
    admin_secret = os.environ.get("ADMIN_SECRET")
    if not admin_secret:
        raise HTTPException(status_code=503, detail="管理機能が無効です（ADMIN_SECRETが未設定）")

    expected = f"Bearer {admin_secret}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="認証に失敗しました")

    try:
        decoded = base64.b64decode(request.cookies_b64).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=400, detail="cookies_b64のデコードに失敗しました")

    with open(COOKIES_PATH, "w") as f:
        f.write(decoded)

    return {"status": "ok", "message": "Cookieを更新しました"}
