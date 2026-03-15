"use client";

type PitchPoint = {
  time: number;
  note: string | null;
  midi: number | null;
  frequency: number | null;
};

type PitchBarProps = {
  pitches: PitchPoint[];
  duration: number;
  color?: string;
  label?: string;
};

type PitchCompareProps = {
  original: { pitches: PitchPoint[]; duration: number };
  user: { pitches: PitchPoint[]; duration: number };
};

// 音程バー（単体）
export function PitchBar({
  pitches,
  duration,
  color = "#a855f7",
  label = "",
}: PitchBarProps) {
  const voiced = pitches.filter((p) => p.midi !== null);
  if (voiced.length === 0) return null;

  const midiValues = voiced.map((p) => p.midi!);
  const minMidi = Math.floor(Math.min(...midiValues)) - 1;
  const maxMidi = Math.ceil(Math.max(...midiValues)) + 1;
  const range = maxMidi - minMidi || 1;

  // 音名ラベル用
  const noteLabels: { midi: number; name: string }[] = [];
  for (let m = Math.ceil(minMidi); m <= Math.floor(maxMidi); m++) {
    const noteNames = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
    const name = noteNames[m % 12];
    // C, E, G, A のみラベル表示（見やすさのため）
    if (["C", "E", "G", "A"].includes(name)) {
      const octave = Math.floor(m / 12) - 1;
      noteLabels.push({ midi: m, name: `${name}${octave}` });
    }
  }

  return (
    <div className="mt-4">
      {label && (
        <p className="text-xs text-zinc-400 mb-2">{label}</p>
      )}
      <div className="relative bg-white/5 border border-white/10 rounded-lg overflow-hidden h-32">
        {/* 音名のガイドライン */}
        {noteLabels.map((nl) => {
          const y = ((maxMidi - nl.midi) / range) * 100;
          return (
            <div key={nl.midi} className="absolute w-full" style={{ top: `${y}%` }}>
              <div className="border-t border-white/10 w-full" />
              <span className="absolute left-1 text-[9px] text-zinc-500 -translate-y-1/2">
                {nl.name}
              </span>
            </div>
          );
        })}

        {/* ピッチバー */}
        <svg className="w-full h-full" preserveAspectRatio="none" viewBox={`0 0 ${duration} ${range}`}>
          {voiced.map((p, i) => {
            const nextVoiced = voiced[i + 1];
            const barWidth = nextVoiced
              ? nextVoiced.time - p.time
              : duration * 0.01;

            return (
              <rect
                key={i}
                x={p.time}
                y={maxMidi - p.midi!}
                width={Math.max(barWidth * 0.9, duration * 0.005)}
                height={0.3}
                rx={0.1}
                fill={color}
                opacity={0.85}
              />
            );
          })}
        </svg>
      </div>
    </div>
  );
}

// 2つの音程バーを重ねて比較
export function PitchCompare({ original, user }: PitchCompareProps) {
  const origVoiced = original.pitches.filter((p) => p.midi !== null);
  const userVoiced = user.pitches.filter((p) => p.midi !== null);

  if (origVoiced.length === 0 && userVoiced.length === 0) return null;

  const allMidi = [
    ...origVoiced.map((p) => p.midi!),
    ...userVoiced.map((p) => p.midi!),
  ];
  const minMidi = Math.floor(Math.min(...allMidi)) - 1;
  const maxMidi = Math.ceil(Math.max(...allMidi)) + 1;
  const range = maxMidi - minMidi || 1;
  const maxDuration = Math.max(original.duration, user.duration);

  const noteLabels: { midi: number; name: string }[] = [];
  for (let m = Math.ceil(minMidi); m <= Math.floor(maxMidi); m++) {
    const noteNames = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
    const name = noteNames[m % 12];
    if (["C", "E", "G", "A"].includes(name)) {
      const octave = Math.floor(m / 12) - 1;
      noteLabels.push({ midi: m, name: `${name}${octave}` });
    }
  }

  return (
    <div className="mt-4">
      <p className="text-xs text-zinc-400 mb-2">音程比較</p>
      <div className="flex gap-4 mb-2 text-xs">
        <span className="flex items-center gap-1">
          <span className="w-3 h-2 rounded-sm bg-purple-500 inline-block" /> 原曲
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-2 rounded-sm bg-pink-500 inline-block" /> あなた
        </span>
      </div>
      <div className="relative bg-white/5 border border-white/10 rounded-lg overflow-hidden h-40">
        {noteLabels.map((nl) => {
          const y = ((maxMidi - nl.midi) / range) * 100;
          return (
            <div key={nl.midi} className="absolute w-full" style={{ top: `${y}%` }}>
              <div className="border-t border-white/10 w-full" />
              <span className="absolute left-1 text-[9px] text-zinc-500 -translate-y-1/2">
                {nl.name}
              </span>
            </div>
          );
        })}

        <svg className="w-full h-full" preserveAspectRatio="none" viewBox={`0 0 ${maxDuration} ${range}`}>
          {/* 原曲（紫） */}
          {origVoiced.map((p, i) => {
            const next = origVoiced[i + 1];
            const w = next ? next.time - p.time : maxDuration * 0.01;
            return (
              <rect
                key={`o-${i}`}
                x={p.time}
                y={maxMidi - p.midi!}
                width={Math.max(w * 0.9, maxDuration * 0.005)}
                height={0.3}
                rx={0.1}
                fill="#a855f7"
                opacity={0.7}
              />
            );
          })}
          {/* ユーザー（ピンク） */}
          {userVoiced.map((p, i) => {
            const next = userVoiced[i + 1];
            const w = next ? next.time - p.time : maxDuration * 0.01;
            return (
              <rect
                key={`u-${i}`}
                x={p.time}
                y={maxMidi - p.midi!}
                width={Math.max(w * 0.9, maxDuration * 0.005)}
                height={0.3}
                rx={0.1}
                fill="#ec4899"
                opacity={0.7}
              />
            );
          })}
        </svg>
      </div>
    </div>
  );
}
