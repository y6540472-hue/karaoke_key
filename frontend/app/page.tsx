"use client";

import { useState, useRef, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

type KeyResult = {
  key: string;
  key_index: number;
  mode: string;
  confidence: number;
};

type SearchResult = {
  id: string;
  title: string;
  url: string;
  thumbnail: string;
  channel: string;
  duration: string | null;
};

// タブの種類
type Tab = "analyze" | "compare";
// 曲入力の方法
type InputMode = "url" | "search";

export default function Home() {
  const [tab, setTab] = useState<Tab>("analyze");

  // --- 共通 ---
  const [error, setError] = useState("");

  // --- 解析タブ ---
  const [inputMode, setInputMode] = useState<InputMode>("url");
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [songKey, setSongKey] = useState<KeyResult | null>(null);
  const [analyzingYoutube, setAnalyzingYoutube] = useState(false);
  const [selectedSong, setSelectedSong] = useState<SearchResult | null>(null);

  const [voiceKey, setVoiceKey] = useState<KeyResult | null>(null);
  const [recording, setRecording] = useState(false);
  const [analyzingVoice, setAnalyzingVoice] = useState(false);

  // --- 比較タブ ---
  const [originalUrl, setOriginalUrl] = useState("");
  const [coverUrl, setCoverUrl] = useState("");
  const [originalKey, setOriginalKey] = useState<KeyResult | null>(null);
  const [coverKey, setCoverKey] = useState<KeyResult | null>(null);
  const [analyzingOriginal, setAnalyzingOriginal] = useState(false);
  const [analyzingCover, setAnalyzingCover] = useState(false);
  // 比較タブでも検索を使えるように
  const [compareSearchQuery, setCompareSearchQuery] = useState("");
  const [compareSearchResults, setCompareSearchResults] = useState<SearchResult[]>([]);
  const [compareSearching, setCompareSearching] = useState(false);
  const [compareTarget, setCompareTarget] = useState<"original" | "cover">("original");

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // YouTube解析
  const analyzeUrl = async (
    url: string,
    setLoading: (v: boolean) => void,
    setResult: (v: KeyResult | null) => void
  ) => {
    if (!url.trim()) return;
    setError("");
    setLoading(true);
    setResult(null);

    try {
      const res = await fetch(`${API_BASE}/api/analyze-youtube`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "解析に失敗しました");
      }
      const data: KeyResult = await res.json();
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "エラーが発生しました");
    } finally {
      setLoading(false);
    }
  };

  // YouTube検索
  const searchYoutube = async (
    query: string,
    setLoading: (v: boolean) => void,
    setResults: (v: SearchResult[]) => void
  ) => {
    if (!query.trim()) return;
    setError("");
    setLoading(true);
    setResults([]);

    try {
      const res = await fetch(
        `${API_BASE}/api/search?q=${encodeURIComponent(query)}`
      );
      if (!res.ok) throw new Error("検索に失敗しました");
      const data: SearchResult[] = await res.json();
      setResults(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "エラーが発生しました");
    } finally {
      setLoading(false);
    }
  };

  // 検索結果から曲を選択（解析タブ）
  const selectSong = (song: SearchResult) => {
    setSelectedSong(song);
    setYoutubeUrl(song.url);
    setSearchResults([]);
    setSearchQuery("");
  };

  // 検索結果から曲を選択（比較タブ）
  const selectCompareSong = (song: SearchResult) => {
    if (compareTarget === "original") {
      setOriginalUrl(song.url);
    } else {
      setCoverUrl(song.url);
    }
    setCompareSearchResults([]);
    setCompareSearchQuery("");
  };

  // マイク録音開始
  const startRecording = useCallback(async () => {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mediaRecorder.start();
      setRecording(true);
    } catch {
      setError("マイクへのアクセスが許可されていません");
    }
  }, []);

  // マイク録音停止 → 解析
  const stopRecording = useCallback(async () => {
    const mediaRecorder = mediaRecorderRef.current;
    if (!mediaRecorder) return;
    setRecording(false);
    setAnalyzingVoice(true);

    mediaRecorder.onstop = async () => {
      const blob = new Blob(chunksRef.current, { type: "audio/webm" });
      const formData = new FormData();
      formData.append("file", blob, "recording.webm");

      try {
        const res = await fetch(`${API_BASE}/api/analyze-voice`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.detail || "解析に失敗しました");
        }
        const data: KeyResult = await res.json();
        setVoiceKey(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "エラーが発生しました");
      } finally {
        setAnalyzingVoice(false);
      }
      mediaRecorder.stream.getTracks().forEach((t) => t.stop());
    };
    mediaRecorder.stop();
  }, []);

  // キー差の計算
  const calcDiff = (
    a: KeyResult | null,
    b: KeyResult | null
  ): { diff: number; recommendation: string } | null => {
    if (!a || !b) return null;
    let diff = b.key_index - a.key_index;
    if (diff > 6) diff -= 12;
    if (diff < -6) diff += 12;

    let recommendation: string;
    if (diff === 0) {
      recommendation = "同じキーです！そのまま歌えます。";
    } else if (diff > 0) {
      recommendation = `カラオケで +${diff} に設定しましょう。`;
    } else {
      recommendation = `カラオケで ${diff} に設定しましょう。`;
    }
    return { diff, recommendation };
  };

  const keyDiff = calcDiff(songKey, voiceKey);
  const compareDiff = calcDiff(originalKey, coverKey);

  // 検索結果コンポーネント
  const SearchResultList = ({
    results,
    onSelect,
  }: {
    results: SearchResult[];
    onSelect: (s: SearchResult) => void;
  }) => (
    <div className="mt-3 max-h-80 overflow-y-auto space-y-2">
      {results.map((r) => (
        <button
          key={r.id}
          onClick={() => onSelect(r)}
          className="w-full flex items-start gap-3 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg p-3 text-left transition-colors"
        >
          <img
            src={r.thumbnail}
            alt=""
            className="w-24 h-16 object-cover rounded flex-shrink-0"
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium line-clamp-2">{r.title}</p>
            <p className="text-xs text-zinc-400 mt-1">{r.channel}</p>
            {r.duration && (
              <p className="text-xs text-zinc-500 mt-0.5">{r.duration}</p>
            )}
          </div>
        </button>
      ))}
    </div>
  );

  // キー結果の表示コンポーネント
  const KeyDisplay = ({
    label,
    result,
    color,
  }: {
    label: string;
    result: KeyResult;
    color: "purple" | "pink" | "blue";
  }) => {
    const colors = {
      purple: "bg-purple-600/20 border-purple-500/30 text-purple-300",
      pink: "bg-pink-600/20 border-pink-500/30 text-pink-300",
      blue: "bg-blue-600/20 border-blue-500/30 text-blue-300",
    };
    return (
      <div className={`mt-4 border rounded-lg p-4 ${colors[color]}`}>
        <p className="text-sm text-zinc-300">{label}</p>
        <p className={`text-2xl font-bold ${colors[color].split(" ")[2]}`}>
          {result.key}
        </p>
        <p className="text-xs text-zinc-400 mt-1">
          信頼度: {Math.round(result.confidence * 100)}%
        </p>
      </div>
    );
  };

  // 差分結果の表示
  const DiffDisplay = ({
    diff,
    recommendation,
    labelA,
    labelB,
    keyA,
    keyB,
  }: {
    diff: number;
    recommendation: string;
    labelA: string;
    labelB: string;
    keyA: string;
    keyB: string;
  }) => (
    <section className="mt-6 bg-gradient-to-br from-purple-600/30 to-pink-600/30 rounded-2xl p-6 border border-white/20">
      <h2 className="text-lg font-semibold mb-4 text-center">
        🎶 おすすめのキー設定
      </h2>
      <div className="text-center">
        <p className="text-5xl font-bold mb-3">
          {diff === 0 ? "±0" : diff > 0 ? `+${diff}` : `${diff}`}
        </p>
        <p className="text-zinc-300">{recommendation}</p>
        <div className="mt-4 flex justify-center gap-6 text-sm text-zinc-400">
          <span>
            {labelA}: {keyA}
          </span>
          <span>→</span>
          <span>
            {labelB}: {keyB}
          </span>
        </div>
      </div>
    </section>
  );

  return (
    <div className="min-h-screen p-6 flex flex-col items-center">
      <div className="w-full max-w-lg">
        {/* ヘッダー */}
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold mb-2">🎤 Karaoke Key</h1>
          <p className="text-zinc-400 text-sm">
            あなたに合ったカラオケのキー設定を見つけよう
          </p>
        </div>

        {/* タブ切替 */}
        <div className="flex mb-6 bg-white/5 rounded-xl p-1 border border-white/10">
          <button
            onClick={() => setTab("analyze")}
            className={`flex-1 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              tab === "analyze"
                ? "bg-purple-600 text-white"
                : "text-zinc-400 hover:text-white"
            }`}
          >
            🎙 キー解析
          </button>
          <button
            onClick={() => setTab("compare")}
            className={`flex-1 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              tab === "compare"
                ? "bg-blue-600 text-white"
                : "text-zinc-400 hover:text-white"
            }`}
          >
            🔀 カバー曲比較
          </button>
        </div>

        {/* ======== 解析タブ ======== */}
        {tab === "analyze" && (
          <>
            {/* ステップ1 */}
            <section className="mb-6 bg-white/5 rounded-2xl p-6 border border-white/10">
              <h2 className="text-lg font-semibold mb-1 flex items-center gap-2">
                <span className="bg-purple-600 text-white text-xs font-bold w-6 h-6 rounded-full flex items-center justify-center">
                  1
                </span>
                歌いたい曲を入力
              </h2>

              {/* URL / 検索 切替 */}
              <div className="flex gap-2 mt-3 mb-4">
                <button
                  onClick={() => setInputMode("url")}
                  className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                    inputMode === "url"
                      ? "bg-purple-600/30 border-purple-500 text-purple-300"
                      : "border-white/20 text-zinc-400 hover:text-white"
                  }`}
                >
                  URLを貼る
                </button>
                <button
                  onClick={() => setInputMode("search")}
                  className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                    inputMode === "search"
                      ? "bg-purple-600/30 border-purple-500 text-purple-300"
                      : "border-white/20 text-zinc-400 hover:text-white"
                  }`}
                >
                  曲を検索
                </button>
              </div>

              {inputMode === "url" ? (
                <div className="flex gap-2">
                  <input
                    type="url"
                    placeholder="https://www.youtube.com/watch?v=..."
                    value={youtubeUrl}
                    onChange={(e) => setYoutubeUrl(e.target.value)}
                    className="flex-1 bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-sm placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-purple-500"
                  />
                  <button
                    onClick={() =>
                      analyzeUrl(youtubeUrl, setAnalyzingYoutube, setSongKey)
                    }
                    disabled={analyzingYoutube || !youtubeUrl.trim()}
                    className="bg-purple-600 hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-5 py-3 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                  >
                    {analyzingYoutube ? "解析中..." : "解析"}
                  </button>
                </div>
              ) : (
                <>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="曲名やアーティスト名で検索..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter")
                          searchYoutube(
                            searchQuery,
                            setSearching,
                            setSearchResults
                          );
                      }}
                      className="flex-1 bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-sm placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-purple-500"
                    />
                    <button
                      onClick={() =>
                        searchYoutube(
                          searchQuery,
                          setSearching,
                          setSearchResults
                        )
                      }
                      disabled={searching || !searchQuery.trim()}
                      className="bg-purple-600 hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-5 py-3 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                    >
                      {searching ? "検索中..." : "検索"}
                    </button>
                  </div>
                  {searchResults.length > 0 && (
                    <SearchResultList
                      results={searchResults}
                      onSelect={(s) => {
                        selectSong(s);
                        analyzeUrl(s.url, setAnalyzingYoutube, setSongKey);
                      }}
                    />
                  )}
                </>
              )}

              {selectedSong && inputMode === "search" && (
                <div className="mt-3 flex items-center gap-3 bg-white/5 rounded-lg p-2">
                  <img
                    src={selectedSong.thumbnail}
                    alt=""
                    className="w-16 h-10 object-cover rounded"
                  />
                  <p className="text-xs text-zinc-300 line-clamp-2">
                    {selectedSong.title}
                  </p>
                </div>
              )}

              {songKey && <KeyDisplay label="原曲のキー" result={songKey} color="purple" />}
            </section>

            {/* ステップ2 */}
            <section className="mb-6 bg-white/5 rounded-2xl p-6 border border-white/10">
              <h2 className="text-lg font-semibold mb-1 flex items-center gap-2">
                <span className="bg-pink-600 text-white text-xs font-bold w-6 h-6 rounded-full flex items-center justify-center">
                  2
                </span>
                あなたの声で歌ってみよう
              </h2>
              <p className="text-zinc-400 text-xs mb-4">
                同じ曲のサビを鼻歌でOK（10〜15秒くらい）
              </p>
              <button
                onClick={recording ? stopRecording : startRecording}
                disabled={analyzingVoice}
                className={`w-full py-4 rounded-lg text-sm font-medium transition-all ${
                  recording
                    ? "bg-red-600 hover:bg-red-700 animate-pulse"
                    : "bg-pink-600 hover:bg-pink-700"
                } disabled:opacity-50 disabled:cursor-not-allowed text-white`}
              >
                {analyzingVoice
                  ? "解析中..."
                  : recording
                  ? "⏹ 録音停止"
                  : "🎙 録音開始"}
              </button>
              {voiceKey && (
                <KeyDisplay label="あなたのキー" result={voiceKey} color="pink" />
              )}
            </section>

            {/* 結果 */}
            {keyDiff && (
              <DiffDisplay
                diff={keyDiff.diff}
                recommendation={keyDiff.recommendation}
                labelA="原曲"
                labelB="あなた"
                keyA={songKey!.key}
                keyB={voiceKey!.key}
              />
            )}
          </>
        )}

        {/* ======== 比較タブ ======== */}
        {tab === "compare" && (
          <>
            <section className="mb-6 bg-white/5 rounded-2xl p-6 border border-white/10">
              <h2 className="text-lg font-semibold mb-4">原曲とカバー曲のキーを比較</h2>
              <p className="text-zinc-400 text-xs mb-4">
                原曲とカバー曲のYouTubeリンクを入力すると、キーの差を計算します
              </p>

              {/* 原曲 */}
              <label className="text-sm text-zinc-300 mb-1 block">原曲</label>
              <div className="flex gap-2 mb-4">
                <input
                  type="url"
                  placeholder="原曲のYouTubeリンク"
                  value={originalUrl}
                  onChange={(e) => setOriginalUrl(e.target.value)}
                  className="flex-1 bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-sm placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-purple-500"
                />
                <button
                  onClick={() =>
                    analyzeUrl(originalUrl, setAnalyzingOriginal, setOriginalKey)
                  }
                  disabled={analyzingOriginal || !originalUrl.trim()}
                  className="bg-purple-600 hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-3 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                >
                  {analyzingOriginal ? "解析中..." : "解析"}
                </button>
              </div>
              {originalKey && (
                <KeyDisplay label="原曲のキー" result={originalKey} color="purple" />
              )}

              {/* カバー曲 */}
              <label className="text-sm text-zinc-300 mb-1 block mt-6">
                カバー曲
              </label>
              <div className="flex gap-2 mb-4">
                <input
                  type="url"
                  placeholder="カバー曲のYouTubeリンク"
                  value={coverUrl}
                  onChange={(e) => setCoverUrl(e.target.value)}
                  className="flex-1 bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-sm placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <button
                  onClick={() =>
                    analyzeUrl(coverUrl, setAnalyzingCover, setCoverKey)
                  }
                  disabled={analyzingCover || !coverUrl.trim()}
                  className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-3 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                >
                  {analyzingCover ? "解析中..." : "解析"}
                </button>
              </div>
              {coverKey && (
                <KeyDisplay label="カバー曲のキー" result={coverKey} color="blue" />
              )}

              {/* 検索ヘルパー */}
              <div className="mt-6 border-t border-white/10 pt-4">
                <p className="text-xs text-zinc-400 mb-2">曲を検索して入力</p>
                <div className="flex gap-2 mb-2">
                  <select
                    value={compareTarget}
                    onChange={(e) =>
                      setCompareTarget(e.target.value as "original" | "cover")
                    }
                    className="bg-white/10 border border-white/20 rounded-lg px-3 py-2 text-sm text-zinc-300"
                  >
                    <option value="original">原曲に入力</option>
                    <option value="cover">カバー曲に入力</option>
                  </select>
                  <input
                    type="text"
                    placeholder="曲名やアーティスト名..."
                    value={compareSearchQuery}
                    onChange={(e) => setCompareSearchQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter")
                        searchYoutube(
                          compareSearchQuery,
                          setCompareSearching,
                          setCompareSearchResults
                        );
                    }}
                    className="flex-1 bg-white/10 border border-white/20 rounded-lg px-4 py-2 text-sm placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <button
                    onClick={() =>
                      searchYoutube(
                        compareSearchQuery,
                        setCompareSearching,
                        setCompareSearchResults
                      )
                    }
                    disabled={compareSearching || !compareSearchQuery.trim()}
                    className="bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm transition-colors"
                  >
                    {compareSearching ? "..." : "検索"}
                  </button>
                </div>
                {compareSearchResults.length > 0 && (
                  <SearchResultList
                    results={compareSearchResults}
                    onSelect={selectCompareSong}
                  />
                )}
              </div>
            </section>

            {/* 比較結果 */}
            {compareDiff && (
              <DiffDisplay
                diff={compareDiff.diff}
                recommendation={compareDiff.recommendation}
                labelA="原曲"
                labelB="カバー"
                keyA={originalKey!.key}
                keyB={coverKey!.key}
              />
            )}
          </>
        )}

        {/* エラー表示 */}
        {error && (
          <div className="mt-4 bg-red-600/20 border border-red-500/30 rounded-lg p-4 text-red-300 text-sm">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
