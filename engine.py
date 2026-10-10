"""Multivac Hero: audio onset detection, MIDI reference retrieval and MIDI export."""
from __future__ import annotations
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import librosa
import mido
import numpy as np

TICKS_PER_BEAT = 480
FRET_START = 96  # Five-fret Expert guitar: green=96 ... orange=100


@dataclass
class AudioResult:
    bpm: float
    onset_seconds: list[float]
    strength: list[float]
    duration: float


def analyze_audio(path: str | Path, limit_seconds: float | None = 30, max_notes_per_second: float = 4.5) -> AudioResult:
    """Estimate attack times. A quick MVP, NOT a reliable guitar transcription."""
    y, sr = librosa.load(str(path), sr=22050, mono=True, duration=limit_seconds)
    duration = len(y) / sr
    if not len(y):
        raise ValueError('Audio file was empty')
    hop = 256
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onset_frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop, backtrack=False)
    times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop)
    # Beat tracker may lock onto half/double tempo. Both are valid grids for our absolute-time export.
    tempo, _ = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=hop, trim=False)
    bpm = float(np.asarray(tempo).reshape(-1)[0]) if np.asarray(tempo).size else 120.0
    if not np.isfinite(bpm) or bpm < 30 or bpm > 300:
        bpm = 120.0
    candidates = sorted((float(t), float(env[min(int(fr), len(env)-1)])) for t, fr in zip(times, onset_frames) if t < duration - .04)
    # Use minimum temporal separation to avoid unreadably dense charts.
    gap = 1.0 / max(1.0, max_notes_per_second)
    selected: list[tuple[float, float]] = []
    for t, strength in candidates:
        if selected and t - selected[-1][0] < gap:
            if strength > selected[-1][1]:
                selected[-1] = (t, strength)
        else:
            selected.append((t, strength))
    return AudioResult(bpm=bpm, onset_seconds=[round(t, 4) for t, _ in selected], strength=[s for _, s in selected], duration=duration)


def reference_patterns(refdir: str | Path, window: int = 12) -> list[dict[str, Any]]:
    """Read Expert five-fret events from MIDI reference files and split into windows."""
    patterns = []
    for file in sorted(Path(refdir).rglob('*.mid')):
        try:
            midi = mido.MidiFile(str(file))
            for track in midi.tracks:
                if track.name.strip().upper() != 'PART GUITAR':
                    continue
                tick = 0
                by_tick: dict[int, set[int]] = {}
                for msg in track:
                    tick += msg.time
                    if msg.type == 'note_on' and msg.velocity > 0 and 96 <= msg.note <= 100:
                        by_tick.setdefault(tick, set()).add(msg.note - FRET_START)
                # Ignore chords in this MVP's monophonic generator but retain first fret as a reference.
                events = [(t/midi.ticks_per_beat, min(frets)) for t, frets in sorted(by_tick.items())]
                for offset in range(0, len(events), window):
                    section = events[offset:offset+window]
                    if len(section) < 4:
                        continue
                    first = section[0][0]
                    patterns.append({'source': file.name, 'rhythm': [round(b-first, 3) for b, _ in section], 'frets':[f for _, f in section]})
        except (OSError, ValueError, EOFError) as exc:
            print(f'Warning: skipped reference {file.name}: {exc}')
    return patterns


def retrieve(onsets: list[float], bpm: float, patterns: list[dict], k: int = 3) -> list[dict]:
    """Cheap rhythm similarity. Compare normalized inter-onset gaps in beat units."""
    if not onsets:
        return []
    q = np.diff(np.asarray(onsets)) * bpm / 60
    def score(item):
        p = np.diff(np.asarray(item['rhythm'], dtype=float))
        n = min(len(q), len(p))
        if n == 0:
            return float('inf')
        mismatch = float(np.mean(np.abs(np.clip(q[:n],0,4)-np.clip(p[:n],0,4))))
        return mismatch + abs(len(item['frets'])-len(onsets)) * .04
    return sorted(patterns, key=score)[:k]


def fallback_frets(n: int, refs: list[dict], last_fret: int = 0) -> list[int]:
    if not refs:
        return [(last_fret + i) % 5 for i in range(n)]
    base = refs[0]['frets']
    return [int(base[i % len(base)]) for i in range(n)]


def llm_frets(times: list[float], bpm: float, refs: list[dict], model: str, previous: list[int]) -> list[int]:
    """LLM selects only frets; Python retains all times. Falls back on ANY API/validation failure."""
    fallback = fallback_frets(len(times), refs, previous[-1] if previous else 0)
    if not os.environ.get('OPENAI_API_KEY') or not model:
        return fallback
    try:
        from openai import OpenAI
        client = OpenAI(timeout=45, max_retries=1)
        payload = {'tempo_bpm': round(bpm, 2), 'event_times_relative_seconds':[round(t-times[0],3) for t in times],
                   'reference_patterns':refs, 'previous_frets':previous[-6:]}
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {'role':'system','content':'You assign Clone Hero 5-fret Expert lanes to fixed-time audio events. Use human-authored reference charts as style examples, never as exact timing labels. Return a JSON object with one array "frets": integers 0 through 4, exactly one per supplied event, with logical playable patterns. Do not add or delete events.'},
                {'role':'user','content':json.dumps(payload)}
            ], response_format={'type':'json_object'}, temperature=0.3)
        obj = json.loads(completion.choices[0].message.content)
        frets = obj['frets']
        if not isinstance(frets,list) or len(frets) != len(times) or any(type(v) is not int or not 0 <= v <= 4 for v in frets):
            raise ValueError('Invalid LLM frets')
        return frets
    except Exception as exc:
        print(f'LLM unavailable/invalid ({exc}); using reference-pattern fallback')
        return fallback


def generate_frets(audio: AudioResult, patterns: list[dict], use_llm: bool = False, model: str='gpt-4o-mini', chunk_size: int=12) -> tuple[list[int], list[dict]]:
    all_frets=[]
    cited=[]
    for offset in range(0,len(audio.onset_seconds),chunk_size):
        times=audio.onset_seconds[offset:offset+chunk_size]
        refs=retrieve(times, audio.bpm, patterns)
        cited.extend([p['source'] for p in refs])
        mapped=llm_frets(times,audio.bpm,refs,model,all_frets) if use_llm else fallback_frets(len(times),refs,all_frets[-1] if all_frets else 0)
        all_frets.extend(mapped)
    return all_frets, [{'reference':s} for s in sorted(set(cited))]


def write_midi(destination: str | Path, bpm: float, times: list[float], frets: list[int]) -> None:
    if len(times)!=len(frets):
        raise ValueError('Time and fret list lengths differ')
    midi=mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    tempo=int(mido.bpm2tempo(bpm))
    timing=mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=tempo,time=0),mido.MetaMessage('end_of_track',time=0)])
    midi.tracks.append(timing)
    guitar=mido.MidiTrack([mido.MetaMessage('track_name',name='PART GUITAR',time=0)])
    events=[]
    for seconds,fret in zip(times,frets):
        if not (0 <= fret <= 4):
            raise ValueError(f'Invalid fret: {fret}')
        tick=max(0,round(seconds*bpm/60*TICKS_PER_BEAT))
        duration=max(20, round(.075*bpm/60*TICKS_PER_BEAT))
        note=FRET_START+fret
        events.append((tick,1,note))
        events.append((tick+duration,0,note))
    previous=0
    for tick, is_on,note in sorted(events, key=lambda e:(e[0],e[1])):
        guitar.append(mido.Message('note_on' if is_on else 'note_off',note=note,velocity=100 if is_on else 0,time=tick-previous))
        previous=tick
    guitar.append(mido.MetaMessage('end_of_track',time=0))
    midi.tracks.append(guitar)
    midi.save(str(destination))


def safe_name(value:str)->str:
    return re.sub(r'[^\w .()\-]', '_', value).strip()[:80] or 'Untitled'


def build_song(audio_file: str | Path, ref_dir: str | Path, output_dir: str | Path, title:str='Generated Song', artist:str='Unknown',
               limit_seconds:float|None=30, use_llm:bool=False,model:str='gpt-4o-mini', max_notes_per_second:float=4.5) -> dict[str, Any]:
    audio_file=Path(audio_file)
    root=Path(output_dir)
    root.mkdir(parents=True,exist_ok=True)
    audio=analyze_audio(audio_file,limit_seconds,max_notes_per_second)
    if not audio.onset_seconds:
        raise ValueError('No onsets detected; please use a different audio recording')
    patterns=reference_patterns(ref_dir)
    frets, references=generate_frets(audio,patterns,use_llm,model)
    folder=root/safe_name(f'{artist} - {title}')
    folder.mkdir(parents=True,exist_ok=True)
    write_midi(folder/'notes.mid',audio.bpm,audio.onset_seconds,frets)
    # Keep original music file format; Clone Hero supports common MP3/OGG/WAV variants.
    target_audio=folder/f'song{audio_file.suffix.lower()}'
    shutil.copyfile(audio_file,target_audio)
    ini=f'[song]\nname = {safe_name(title)}\nartist = {safe_name(artist)}\ncharter = Multivac Hero\n' \
        'diff_guitar = 3\n' \
        f'song_length = {round(librosa.get_duration(path=str(audio_file))*1000)}\n'
    (folder/'song.ini').write_text(ini,encoding='utf-8')
    report={'title':title,'artist':artist,'bpm':round(audio.bpm,2),'detected_onsets':len(audio.onset_seconds),
            'reference_patterns':len(patterns),'retrieved_sources':references,'mode':('LLM requested (may fall back; consult log)' if use_llm and os.environ.get('OPENAI_API_KEY') else 'reference-pattern baseline'),
            'note':'Timing estimated from audio onsets; generated chart has Expert guitar only.'}
    (folder/'generation_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    zip_path=root/f'{folder.name}.zip'
    with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in folder.iterdir():
            archive.write(path,arcname=f'{folder.name}/{path.name}')
    report.update({'zip_path':str(zip_path),'folder':str(folder)})
    return report
