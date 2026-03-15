import os
import json
import tempfile
import subprocess
import numpy as np
import librosa
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Karaoke Key Analyzer")

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 音楽のキー名（メジャー / マイナー）
KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


import shutil

YT_DLP_PATH = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"


def get_env():
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


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


def detect_key(audio_path: str) -> KeyResult:
    """音声ファイルからキー（調）を検出する"""
    y, sr = librosa.load(audio_path, sr=22050, duration=60)

    # クロマグラム（各音階の強さ）を計算
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)

    # Krumhansl-Kessler のキープロファイルで照合
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
    # 最短距離を選ぶ（例: +7 → -5）
    if diff > 6:
        diff -= 12
    elif diff < -6:
        diff += 12
    return diff


@app.post("/api/analyze-youtube", response_model=KeyResult)
async def analyze_youtube(request: YouTubeRequest):
    """YouTubeリンクから曲のキーを解析する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "audio.wav")
        try:
            result = subprocess.run(
                [
                    YT_DLP_PATH,
                    "-x",
                    "--audio-format", "wav",
                    "--audio-quality", "0",
                    "-o", output_path,
                    "--no-playlist",
                    request.url,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                env=get_env(),
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="yt-dlpが見つかりません",
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=408, detail="タイムアウトしました")

        # yt-dlp が拡張子を変えることがある
        actual_files = [f for f in os.listdir(tmpdir) if f.startswith("audio")]
        if not actual_files:
            raise HTTPException(status_code=500, detail="音声ファイルが見つかりません")

        actual_path = os.path.join(tmpdir, actual_files[0])
        result = detect_key(actual_path)
        return result


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


@app.get("/api/search")
async def search_youtube(q: str):
    """YouTube動画を検索する（yt-dlpを使用、APIキー不要）"""
    try:
        result = subprocess.run(
            [
                YT_DLP_PATH,
                f"ytsearch10:{q}",
                "--dump-json",
                "--no-download",
                "--flat-playlist",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=get_env(),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="検索がタイムアウトしました")

    results: list[SearchResult] = []
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

            results.append(SearchResult(
                id=video_id,
                title=data.get("title", ""),
                url=f"https://www.youtube.com/watch?v={video_id}",
                thumbnail=data.get("thumbnails", [{}])[-1].get("url", "")
                    if data.get("thumbnails")
                    else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                channel=data.get("channel", data.get("uploader", "")),
                duration=duration_str,
            ))
        except json.JSONDecodeError:
            continue

    return results


@app.get("/api/health")
async def health():
    return {"status": "ok"}
