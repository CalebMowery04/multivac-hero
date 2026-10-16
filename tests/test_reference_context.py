import json
import os
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mido
import numpy as np
import soundfile as sf
import engine
from reference_context import decode_midi, compact_midi, prepare, RequestSizeError, trim_midi, select_difficulties, available_difficulties


class ReferenceContextTests(unittest.TestCase):
    def expand(self, compact):
        tracks = []
        for track in compact['tracks']:
            deltas = [delta for delta,count in track['delta_ticks_rle'] for _ in range(count)]
            self.assertEqual(len(deltas),len(track['message_ids']))
            events, tick = [], 0
            for delta,index in zip(deltas,track['message_ids']):
                tick += delta
                events.append({'tick':tick,**compact['message_dictionary'][index]})
            tracks.append({'name':track['name'],'events':events})
        return {**{k:v for k,v in compact.items() if k not in ('event_encoding','message_dictionary','tracks')},'tracks':tracks}

    def test_compact_representation_is_lossless(self):
        with tempfile.TemporaryDirectory() as td:
            request = self.fixture(Path(td))
            chart = decode_midi(Path(request['reference_folder']) / 'notes.mid')
            self.assertEqual(self.expand(compact_midi(chart)), chart)

    def fixture(self, root):
        refs = root / 'refs'; refs.mkdir()
        midi = mido.MidiFile()
        midi.tracks.append(mido.MidiTrack([
            mido.MetaMessage('set_tempo', tempo=500000),
            mido.MetaMessage('set_tempo', tempo=600000, time=480)]))
        midi.tracks.append(mido.MidiTrack([
            mido.MetaMessage('track_name', name='PART GUITAR'),
            mido.Message('note_on', note=96, velocity=100, time=120),
            mido.Message('note_on', note=98, velocity=100),
            mido.Message('note_off', note=96, time=240),
            mido.Message('note_off', note=98)]))
        midi.tracks.append(mido.MidiTrack([mido.MetaMessage('lyrics', text='test lyric', time=10)]))
        midi.save(refs / 'notes.mid')
        for name in ('guitar.mp3', 'rhythm.mp3', 'preview.mp3'):
            # WAV content with MP3 filenames keeps fixture creation independent of encoders.
            sf.write(refs / name, np.ones(22050) * .8, 22050, format='WAV')
        target = root / 'target.wav'; sf.write(target, np.zeros(2205), 22050)
        return {'reference_folder': str(refs), 'audio': str(target), 'seconds': 0, 'result': str(root / 'estimate.json')}

    def test_complete_midi_and_aligned_mix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); request = self.fixture(root)
            result = prepare(request)
            self.assertEqual(result['reference_files'], ['guitar.mp3','rhythm.mp3'])
            self.assertNotIn('preview.mp3', result['reference_files'])
            compact = json.loads((root / 'reference_chart.json').read_text())
            chart = self.expand(compact)
            self.assertEqual(len(chart['tracks']), 3)
            self.assertEqual(chart['tracks'][0]['events'][1]['tempo'], 600000)
            self.assertEqual(chart['tracks'][1]['events'][1]['tick'], 120)
            self.assertEqual(chart['tracks'][2]['events'][0]['text'], 'test lyric')
            mix, sr = sf.read(root / 'reference_mix.wav')
            self.assertEqual(len(mix), 22050)
            self.assertLessEqual(np.max(np.abs(mix)), .981)
            self.assertGreater(np.max(mix), .97)
            self.assertGreater(result['estimated_cost_usd'], 0)

    def test_real_worker_prepares_without_api_call(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); request = self.fixture(root)
            request['stage'] = 'prepare'
            request_path = root / 'request.json'
            request_path.write_text(json.dumps(request))
            env = dict(os.environ)
            env.pop('OPENAI_API_KEY', None)
            completed = subprocess.run([sys.executable, 'worker.py', str(request_path)], env=env, capture_output=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            estimate = json.loads(Path(request['result']).read_text())
            self.assertIn('estimated_cost_usd', estimate)
            self.assertTrue((root / 'reference_mix.wav').exists())

    def test_select_reference_difficulties_preserves_pairs_and_shared_events(self):
        events = [{'tick':0,'type':'set_tempo','tempo':500000}]
        for base in (60,72,84,96):
            events.extend([{'tick':10,'type':'note_on','note':base,'velocity':100,'channel':0},
                           {'tick':20,'type':'note_off','note':base,'velocity':0,'channel':0}])
        events.append({'tick':30,'type':'note_on','note':116,'velocity':100,'channel':0})
        chart={'type':1,'ticks_per_beat':480,'tracks':[{'name':'PART GUITAR','events':events}]}
        self.assertEqual(available_difficulties(chart),['easy','medium','hard','expert'])
        for selection, expected in [(['easy'],[60,60]),(['medium','expert'],[72,72,96,96])]:
            filtered=select_difficulties(chart,selection)
            retained=filtered['tracks'][0]['events']
            self.assertEqual([e['note'] for e in retained if e.get('note',116)!=116],expected)
            self.assertEqual(retained[0]['type'],'set_tempo')
            self.assertEqual(retained[-1]['note'],116)
        for invalid in ([],['impossible']):
            with self.assertRaises(ValueError): select_difficulties(chart,invalid)
        expert_only=select_difficulties(chart,['expert'])
        with self.assertRaises(ValueError): select_difficulties(expert_only,['easy'])

    def test_trim_handles_tempo_changes_and_held_notes(self):
        chart = {'type':1,'ticks_per_beat':480,'tracks':[
            {'name':'tempo','events':[{'tick':0,'type':'set_tempo','tempo':500000}, {'tick':480,'type':'set_tempo','tempo':1000000}]},
            {'name':'PART GUITAR','events':[
                {'tick':600,'type':'note_on','channel':0,'note':96,'velocity':100},
                {'tick':600,'type':'note_on','channel':0,'note':98,'velocity':100},
                {'tick':720,'type':'note_on','channel':0,'note':99,'velocity':100},
                {'tick':1000,'type':'note_off','channel':0,'note':96,'velocity':0},
                {'tick':1000,'type':'note_off','channel':0,'note':98,'velocity':0}]}]}
        trimmed = trim_midi(chart,1)
        self.assertEqual(trimmed['clip_end_tick'],720)
        self.assertEqual(trimmed['tracks'][0]['events'][1]['tempo'],1000000)
        notes = trimmed['tracks'][1]['events']
        self.assertEqual([e['note'] for e in notes if e['type']=='note_on'],[96,98])
        self.assertEqual([(e['tick'],e['note']) for e in notes if e['type']=='note_off'],[(720,96),(720,98)])
        self.assertTrue(all(e['tick']<=720 for t in trimmed['tracks'] for e in t['events']))

    def test_durations_change_both_audio_and_midi_estimate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); request = self.fixture(root)
            full = prepare(request)
            request.update(reference_seconds=.05, seconds=.05)
            short = prepare(request)
            audio, sr = sf.read(root / 'reference_mix.wav')
            self.assertAlmostEqual(len(audio)/sr,.05,delta=1/sr)
            chart = self.expand(json.loads((root / 'reference_chart.json').read_text()))
            self.assertFalse(any(e['type']=='note_on' for t in chart['tracks'] for e in t['events']))
            self.assertLess(short['estimated_input_tokens'],full['estimated_input_tokens'])
            self.assertLess(short['estimated_cost_usd'],full['estimated_cost_usd'])
            self.assertEqual(short['requested_reference_seconds'],.05)
            self.assertEqual(short['requested_target_seconds'],.05)

    def test_size_gate_precedes_mixing_or_api(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); request = self.fixture(root)
            with patch('reference_context.MAX_INPUT_TOKENS', 1), patch('reference_context.librosa.load') as load:
                result = prepare(request)
                self.assertTrue(result['size_exceeded'])
                self.assertFalse(result['preview_ready'])
                self.assertIn('midi_text_tokens',result['size_breakdown'])
                self.assertGreater(result['estimated_cost_usd'],0)
                load.assert_not_called()

    @patch.dict(os.environ, {'OPENAI_API_KEY':'test-only'})
    def test_reference_and_target_are_distinct_audio_parts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); request = self.fixture(root)
            estimate = prepare(request)
            (root / 'estimate.json').write_text(json.dumps(estimate))
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'notes':[{'time':.01,'duration':.03,'frets':[0]}]})))])
            with patch('openai.OpenAI') as client:
                client.return_value.chat.completions.create.return_value=response
                notes, outcome = engine.direct_audio_chart(Path(request['audio']), .1, context_dir=root)
                kwargs = client.return_value.chat.completions.create.call_args.kwargs
            self.assertIsNotNone(notes)
            parts = kwargs['messages'][1]['content']
            self.assertEqual(sum(p['type']=='input_audio' for p in parts),2)
            self.assertIn('test lyric',parts[0]['text'])
            self.assertIn('TARGET',parts[2]['text'])
            self.assertEqual(kwargs['max_completion_tokens'],16000)
            self.assertEqual(outcome['reference_context'],'complete_midi_and_full_mix')
