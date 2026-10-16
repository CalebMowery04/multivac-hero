import tempfile
import unittest
from pathlib import Path
import mido
from chart_preview import read_chart

class ChartPreviewTests(unittest.TestCase):
    def test_tempo_changes_chords_sustains_and_track_filter(self):
        midi=mido.MidiFile(ticks_per_beat=480)
        midi.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=500000),mido.MetaMessage('set_tempo',tempo=1000000,time=480)]))
        midi.tracks.append(mido.MidiTrack([
            mido.MetaMessage('track_name',name='PART GUITAR'),
            mido.Message('note_on',note=96,velocity=100,time=480),
            mido.Message('note_on',note=98,velocity=100),
            mido.Message('note_off',note=96,time=480),
            mido.Message('note_on',note=98,velocity=0),
            mido.Message('note_on',note=60,velocity=100),
            mido.Message('note_off',note=60,time=120)]))
        midi.tracks.append(mido.MidiTrack([mido.MetaMessage('track_name',name='PART BASS'),mido.Message('note_on',note=96,velocity=100),mido.Message('note_off',note=96,time=240)]))
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'notes.mid';midi.save(path);chart=read_chart(path)
        self.assertEqual(chart['notes'],[{'time':.5,'duration':1.0,'fret':0},{'time':.5,'duration':1.0,'fret':2}])
        self.assertEqual(chart['chart_end_seconds'],1.5)
