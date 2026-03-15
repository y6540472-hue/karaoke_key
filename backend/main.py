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
import warnings
warnings.filterwarnings("ignore")

app = FastAPI(title="Karaoke Key Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def download_youtube_audio(url: str, output_path: str) -> None:
    """YouTubeから音声をダウンロードする（複数クライアントでリトライ）"""
    # ボット検出を回避するためリアルなUser-Agentを設定
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

    # 複数のクライアントを試す（ボット検出を回避しやすい順）
    client_options = [
        ["--extractor-args", "youtube:player_client=ios"],
        ["--extractor-args", "youtube:player_client=tv_embedded"],
        ["--extractor-args", "youtube:player_client=mweb"],
        ["--extractor-args", "youtube:player_client=web"],
        [],  # デフォルト（フォールバック）
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
            return  # 成功
        last_error = (result.stderr or "") + (result.stdout or "")

        # ダウンロード失敗したファイルを掃除
        import glob
        dir_path = os.path.dirname(output_path)
        for f in glob.glob(os.path.join(dir_path, "*")):
            os.remove(f)

    raise HTTPException(
        status_code=400,
        detail=f"YouTube音声の取得に失敗しました: {last_error[:300]}",
    )


class YouTubeRequest(BaseModel):
    url: str
    section: Optional[str] = "intro"  # "intro" or "chorus"


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


class PitchPoint(BaseModel):
    time: float
    note: Optional[str] = None
    midi: Optional[float] = None
    frequency: Optional[float] = None


class PitchResult(BaseModel):
    key: KeyResult
    pitches: list[PitchPoint]
    duration: float
    start_time: float = 0.0


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


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_note_name(midi_num: float) -> str:
    """MIDIノート番号を音名に変換（例: 60 → C4）"""
    note_idx = int(round(midi_num)) % 12
    octave = int(round(midi_num)) // 12 - 1
    return f"{NOTE_NAMES[note_idx]}{octave}"


def detect_chorus_start(audio_path: str) -> float:
    """曲のサビ開始位置を推定する（秒）"""
    y, sr = librosa.load(audio_path, sr=22050)
    # RMS（音量）の変化からサビを推定
    rms = librosa.feature.rms(y=y)[0]
    times = librosa.times_like(rms, sr=sr)

    # 音量が全体平均より高い区間を探す
    threshold = np.mean(rms) * 1.2
    loud_sections = times[rms > threshold]

    if len(loud_sections) > 0:
        # 最初の盛り上がり（イントロ直後を避けるため10秒以降）
        candidates = loud_sections[loud_sections > 10.0]
        if len(candidates) > 0:
            return float(candidates[0])

    # 見つからなければ曲の1/3あたりを返す
    total_duration = float(len(y) / sr)
    return min(total_duration * 0.3, 60.0)


def detect_pitch_timeline(
    audio_path: str, max_duration: float = 30.0, start_time: float = 0.0
) -> PitchResult:
    """音声からピッチの時系列データを抽出する"""
    y_full, sr = librosa.load(audio_path, sr=22050)
    total_samples = len(y_full)
    start_sample = int(start_time * sr)
    end_sample = min(start_sample + int(max_duration * sr), total_samples)
    y = y_full[start_sample:end_sample]
    duration = float(len(y) / sr)

    # pYINでピッチ検出（声に特化した手法）
    f0, voiced_flag, _ = librosa.pyin(
        y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"), sr=sr
    )
    times = librosa.times_like(f0, sr=sr)

    # 間引き（表示用に最大200ポイントに）
    step = max(1, len(times) // 200)

    pitches: list[PitchPoint] = []
    for i in range(0, len(times), step):
        t = round(float(times[i]), 3)
        if voiced_flag[i] and f0[i] is not None and not np.isnan(f0[i]):
            freq = float(f0[i])
            midi = float(librosa.hz_to_midi(freq))
            pitches.append(PitchPoint(
                time=t,
                note=midi_to_note_name(midi),
                midi=round(midi, 1),
                frequency=round(freq, 1),
            ))
        else:
            pitches.append(PitchPoint(time=t))

    key = detect_key(audio_path)
    return PitchResult(
        key=key, pitches=pitches, duration=round(duration, 2),
        start_time=round(start_time, 2),
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


@app.get("/api/search")
async def search_youtube(q: str, count: int = 10):
    """YouTube動画を検索する（yt-dlpを使用、APIキー不要）"""
    if count > 30:
        count = 30
    try:
        result = subprocess.run(
            [
                YT_DLP_PATH,
                f"ytsearch{count}:{q}",
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


@app.post("/api/pitch-youtube", response_model=PitchResult)
async def pitch_youtube(request: YouTubeRequest):
    """YouTubeリンクから音程の時系列データを取得する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "audio.wav")
        download_youtube_audio(request.url, output_path)

        actual_files = [f for f in os.listdir(tmpdir)]
        if not actual_files:
            raise HTTPException(status_code=500, detail="音声ファイルが見つかりません")

        actual_path = os.path.join(tmpdir, actual_files[0])

        start_time = 0.0
        if request.section == "chorus":
            start_time = detect_chorus_start(actual_path)

        return detect_pitch_timeline(actual_path, start_time=start_time)


@app.post("/api/pitch-voice", response_model=PitchResult)
async def pitch_voice(file: UploadFile = File(...)):
    """マイク録音した音声から音程の時系列データを取得する"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return detect_pitch_timeline(tmp_path)
    finally:
        os.unlink(tmp_path)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
