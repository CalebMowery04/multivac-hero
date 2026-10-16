"""Reference audio prefixes paired with tempo-aware MIDI prefixes and local estimates."""
import io
import json
import os
from pathlib import Path

import librosa
import mido
import numpy as np
import soundfile as sf

# Deliberately conservative planning assumptions, not guaranteed billing token counts.
AUDIO_TOKENS_PER_SECOND = 50
MAX_INPUT_TOKENS = 100_000  # Reserve room inside the model's 128k context.
MAX_OUTPUT_TOKENS = 16_000


def decode_midi(path):
    midi = mido.MidiFile(path)
    if midi.type == 2:
        raise ValueError('Asynchronous type-2 MIDI is not supported')
    tracks = []
    for track in midi.tracks:
        tick = 0
        events = []
        for msg in track:
            tick += msg.time
            event = msg.dict()
            event.pop('time', None)
            if isinstance(event.get('data'), (bytes, bytearray, tuple)):
                event['data'] = list(event['data'])
            events.append({'tick': tick, **event})
        tracks.append({'name': track.name, 'events': events})
    return {'type': midi.type, 'ticks_per_beat': midi.ticks_per_beat,
            'timing': 'Absolute MIDI ticks. Apply set_tempo events; default tempo is 500000 microseconds/beat. Audio begins at zero; no metadata offset supplied.',
            'tracks': tracks}


def trim_midi(chart, seconds):
    """Keep the prefix aligned to audio, using every tempo change across tracks."""
    tempo_events = sorted((event['tick'], ti, ei, event['tempo'])
                          for ti, track in enumerate(chart['tracks'])
                          for ei, event in enumerate(track['events']) if event['type'] == 'set_tempo')
    tick, elapsed, tempo = 0, 0.0, 500000
    for next_tick, _, _, next_tempo in tempo_events:
        interval = mido.tick2second(next_tick - tick, chart['ticks_per_beat'], tempo)
        if elapsed + interval > seconds:
            break
        elapsed += interval
        tick, tempo = next_tick, next_tempo
    cutoff = tick + int(mido.second2tick(max(0, seconds - elapsed), chart['ticks_per_beat'], tempo))
    tracks = []
    for track in chart['tracks']:
        events, active = [], {}
        for event in track['events']:
            if event['tick'] > cutoff:
                break
            if event['type'] == 'end_of_track':
                continue
            is_on = event['type'] == 'note_on' and event['velocity'] > 0
            if is_on and event['tick'] == cutoff:
                continue
            events.append(dict(event))
            if event['type'] in ('note_on', 'note_off'):
                key = (event['channel'], event['note'])
                if is_on:
                    active[key] = active.get(key, 0) + 1
                elif active.get(key, 0):
                    active[key] -= 1
        for (channel, note), count in active.items():
            events.extend({'tick': cutoff, 'type': 'note_off', 'channel': channel, 'note': note, 'velocity': 0} for _ in range(count))
        events.append({'tick': cutoff, 'type': 'end_of_track'})
        tracks.append({'name': track['name'], 'events': events})
    return {**chart, 'tracks': tracks, 'clip_seconds': seconds,
            'clip_end_tick': cutoff, 'clip_note': 'Prefix of reference audio. Notes held across the end are released at the clip boundary.'}


def compact_midi(chart):
    """Lossless dictionary of repeated MIDI messages and run-length encoded deltas."""
    messages, message_ids, tracks = [], {}, []
    for track in chart['tracks']:
        deltas, ids = [], []
        previous = 0
        for event in track['events']:
            message = {k:v for k,v in event.items() if k != 'tick'}
            key = json.dumps(message, sort_keys=True, separators=(',', ':'))
            if key not in message_ids:
                message_ids[key] = len(messages)
                messages.append(message)
            ids.append(message_ids[key])
            delta = event['tick'] - previous
            previous = event['tick']
            if deltas and deltas[-1][0] == delta:
                deltas[-1][1] += 1
            else:
                deltas.append([delta, 1])
        tracks.append({'name': track['name'], 'delta_ticks_rle': deltas, 'message_ids': ids})
    return {**{k:v for k,v in chart.items() if k != 'tracks'},
            'event_encoding': 'Lossless MIDI. Expand each delta_ticks_rle pair [delta_ticks, repeat_count] in order, then zip with message_ids. Sum deltas from tick zero per track. Each message_id indexes the full message in message_dictionary. All tracks and events are retained.',
            'message_dictionary': messages, 'tracks': tracks}


def text_token_count(text):
    try:
        import tiktoken
        os.environ.setdefault('TIKTOKEN_CACHE_DIR', str(Path(__file__).parent / '.cache' / 'tiktoken'))
        return len(tiktoken.get_encoding('o200k_base').encode(text, disallowed_special=())), 'o200k_base'
    except (ImportError, OSError):
        return len(text.encode('utf-8')), 'byte_upper_bound (tokenizer unavailable)'


class RequestSizeError(ValueError):
    def __init__(self, details):
        self.details = details
        super().__init__(details['message'])


DIFFICULTIES = {'easy': 60, 'medium': 72, 'hard': 84, 'expert': 96}


def available_difficulties(chart):
    notes = {e['note'] for track in chart['tracks'] if track['name'].strip().upper() == 'PART GUITAR'
             for e in track['events'] if e['type'] == 'note_on' and e['velocity'] > 0}
    return [name for name, base in DIFFICULTIES.items() if any(base - 1 <= note <= base + 4 for note in notes)]


def select_difficulties(chart, selected):
    """Filter guitar difficulty notes and their per-difficulty markers, retaining shared events."""
    if not isinstance(selected, list) or not selected or any(name not in DIFFICULTIES for name in selected):
        raise ValueError('Choose at least one valid reference difficulty')
    available = available_difficulties(chart)
    if any(name not in available for name in selected):
        raise ValueError('Selected difficulty is not present in the reference guitar chart')
    tracks = []
    for track in chart['tracks']:
        events = []
        for event in track['events']:
            keep = True
            if track['name'].strip().upper() == 'PART GUITAR' and event['type'] in ('note_on', 'note_off'):
                for name, base in DIFFICULTIES.items():
                    if base - 1 <= event['note'] <= base + 10:
                        keep = name in selected
                        break
            if keep:
                events.append(event)
        tracks.append({**track, 'events': events})
    return {**chart, 'tracks': tracks, 'selected_guitar_difficulties': selected,
            'difficulty_encoding': 'Easy lanes 60–64, Medium 72–76, Hard 84–88, Expert 96–100. Open notes are one pitch below each range. Other tracks and shared markers are retained.'}


def prepare(request):
    folder = Path(request['reference_folder'])
    mp3s = sorted(p for p in folder.iterdir() if p.suffix.lower() == '.mp3' and p.stem.lower() != 'preview')
    midis = sorted(p for p in folder.iterdir() if p.suffix.lower() == '.mid')
    if not mp3s or len(midis) != 1:
        raise ValueError('Select at least one MP3 and exactly one MIDI from the reference folder')
    durations = [float(librosa.get_duration(path=str(p))) for p in mp3s]
    target_duration = float(librosa.get_duration(path=request['audio']))
    limit = request['seconds']
    ref_limit = request.get('reference_seconds', 0)
    if any(type(value) not in (int, float) or not np.isfinite(value) or not 0 <= value <= 1800 for value in (limit, ref_limit)):
        raise ValueError('Durations must be between 0 and 1800 seconds; 0 means full audio')
    if limit:
        target_duration = min(target_duration, limit)
    mix_duration = min(max(durations), ref_limit) if ref_limit else max(durations)
    if not all(np.isfinite(d) and 0 < d <= 1800 for d in (mix_duration, target_duration)):
        raise ValueError('Selected audio must be nonempty and at most 30 minutes')
    full_chart = decode_midi(midis[0])
    available = available_difficulties(full_chart)
    selected = request.get('reference_difficulties')
    if selected is None:
        selected = available
    chart = trim_midi(select_difficulties(full_chart, selected), mix_duration)
    original_bytes = len(json.dumps(chart, separators=(',', ':')).encode('utf-8'))
    text = json.dumps(compact_midi(chart), separators=(',', ':'))
    midi_tokens, tokenizer = text_token_count(text)
    text_tokens = midi_tokens + 2000
    estimated_tokens = int((mix_duration + target_duration) * AUDIO_TOKENS_PER_SECOND) + text_tokens
    breakdown = {'reference_audio_tokens': int(mix_duration * AUDIO_TOKENS_PER_SECOND),
                 'target_audio_tokens': int(target_duration * AUDIO_TOKENS_PER_SECOND),
                 'midi_text_tokens': midi_tokens, 'prompt_allowance_tokens': 2000,
                 'estimated_input_tokens': estimated_tokens, 'input_allowance_tokens': MAX_INPUT_TOKENS,
                 'tokenizer': tokenizer, 'original_midi_json_bytes': original_bytes,
                 'compact_midi_json_bytes': len(text.encode('utf-8')),
                 'reference_seconds': mix_duration, 'target_seconds': target_duration}
    audio_tokens = int((mix_duration + target_duration) * AUDIO_TOKENS_PER_SECOND)
    estimate = round(audio_tokens * 32 / 1_000_000 + text_tokens * 2.5 / 1_000_000 + MAX_OUTPUT_TOKENS * 10 / 1_000_000, 4)
    result = {'reference_files': [p.name for p in mp3s], 'midi_file': midis[0].name,
              'reference_seconds': mix_duration, 'target_seconds': target_duration,
              'available_difficulties': available, 'selected_difficulties': selected,
              'requested_reference_seconds': ref_limit, 'requested_target_seconds': limit,
              'estimated_cost_usd': estimate, 'estimated_input_tokens': estimated_tokens,
              'estimated_output_tokens': MAX_OUTPUT_TOKENS,
              'estimated_total_tokens': estimated_tokens + MAX_OUTPUT_TOKENS,
              'size_breakdown': breakdown, 'size_exceeded': estimated_tokens > MAX_INPUT_TOKENS,
              'preview_ready': False,
              'estimate_note': 'No OpenAI call made. Audio: planning estimate of 50 tokens/second. MIDI: local tokenizer count plus 2000 prompt tokens. Output allowance: 16000 tokens. Cost uses $32/M audio input, $2.50/M text input, $10/M text output. Actual usage may differ; this is not a billing cap.'}
    if result['size_exceeded']:
        result['size_message'] = 'Estimated context exceeds the local input allowance. Shorten the reference duration (audio AND MIDI) or target duration and recalculate. Raising your spending limit does not change context capacity.'
        return result
    # Decode full selected tracks at one shared sample rate, preserve zero-time alignment.
    sr = 22050
    mix = np.zeros(int(np.ceil(mix_duration * sr)) + sr, dtype=np.float32)
    actual_length = 0
    for path in mp3s:
        y, _ = librosa.load(str(path), sr=sr, mono=True, duration=mix_duration)
        mix[:len(y)] += y
        actual_length = max(actual_length, len(y))
    mix = mix[:actual_length]
    peak = float(np.max(np.abs(mix)))
    if peak > .98:
        mix *= .98 / peak
    destination = Path(request['result']).parent
    # WAV avoids another lossy encoding and is accepted by the model directly.
    sf.write(destination / 'reference_mix.wav', mix, sr, subtype='PCM_16')
    (destination / 'reference_chart.json').write_text(text, encoding='utf-8')
    return {**result, 'reference_seconds': len(mix) / sr, 'preview_ready': True}


def audio_content(path):
    import base64
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    buffer = io.BytesIO()
    sf.write(buffer, y, sr, format='WAV', subtype='PCM_16')
    return {'type': 'input_audio', 'input_audio': {'data': base64.b64encode(buffer.getvalue()).decode('ascii'), 'format': 'wav'}}
