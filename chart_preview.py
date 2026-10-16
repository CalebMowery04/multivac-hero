"""Read actual exported Expert guitar MIDI into seconds for browser playback."""
from collections import defaultdict, deque
import mido


def read_chart(path):
    midi = mido.MidiFile(path)
    guitar_tracks = [track for track in midi.tracks if track.name.strip().upper() == 'PART GUITAR']
    # Keep tempo events from every track while filtering notes to guitar only.
    tracks = []
    for track in midi.tracks:
        output = mido.MidiTrack()
        pending = 0
        for message in track:
            pending += message.time
            if message.type == 'set_tempo' or (track in guitar_tracks and message.type in ('note_on', 'note_off') and 96 <= message.note <= 100):
                output.append(message.copy(time=pending))
                pending = 0
        tracks.append(output)
    tempo, elapsed = 500000, 0.0
    active = defaultdict(deque)
    notes = []
    for message in mido.merge_tracks(tracks):
        elapsed += mido.tick2second(message.time, midi.ticks_per_beat, tempo)
        if message.type == 'set_tempo':
            tempo = message.tempo
        elif message.type in ('note_on', 'note_off'):
            key = (message.channel, message.note)
            if message.type == 'note_on' and message.velocity > 0:
                active[key].append(elapsed)
            elif active[key]:
                start = active[key].popleft()
                notes.append({'time': start, 'duration': max(0, elapsed-start), 'fret': message.note-96})
    if any(active.values()):
        raise ValueError('Preview MIDI contains notes without note-off events')
    notes.sort(key=lambda n: (n['time'], n['fret']))
    return {'difficulty': 'Expert', 'instrument': 'guitar', 'notes': notes,
            'chart_end_seconds': max((n['time']+n['duration'] for n in notes), default=0)}
