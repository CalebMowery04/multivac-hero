import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mido
import numpy as np
import soundfile as sf
import engine


class DirectAudioTests(unittest.TestCase):
    def test_validation(self):
        valid = [{'time': .2, 'duration': .5, 'frets': [0, 2]}]
        self.assertEqual(engine.validate_audio_chart({'notes': valid}, 1), valid)
        for bad in ([], [{'time': -1, 'duration': .1, 'frets': [0]}],
                    [{'time': .2, 'duration': .1, 'frets': [True]}],
                    [{'time': .2, 'duration': 2, 'frets': [0]}],
                    valid + [{'time': .4, 'duration': .1, 'frets': [2]}],
                    [{'time': float('nan'), 'duration': .1, 'frets': [0]}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                engine.validate_audio_chart({'notes': bad}, 1)

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'})
    def test_audio_payload_and_export(self):
        notes = [{'time': .2, 'duration': .5, 'frets': [0, 2]}]
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'notes': notes})))])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / 'audio.wav'
            sf.write(src, np.zeros(22050), 22050)
            with patch('openai.OpenAI') as client, patch('engine.analyze_audio', return_value=engine.AudioResult(123.4, [], [], 1)):
                client.return_value.chat.completions.create.return_value = response
                report = engine.build_song(src, root / 'refs', root / 'out', direct_audio=True)
                kwargs = client.return_value.chat.completions.create.call_args.kwargs
            self.assertEqual(kwargs['model'], 'gpt-audio-1.5')
            self.assertEqual(kwargs['modalities'], ['text'])
            self.assertNotIn('response_format', kwargs)
            data = kwargs['messages'][1]['content'][1]['input_audio']['data']
            y, sr = sf.read(io.BytesIO(base64.b64decode(data)))
            self.assertEqual(len(y), 22050)
            self.assertEqual(sr, 22050)
            self.assertEqual(report['mode'], 'llm')
            self.assertEqual(report['exported_notes'], 2)
            elapsed = 0
            on, off = [], []
            for msg in mido.MidiFile(Path(report['folder']) / 'notes.mid'):
                elapsed += msg.time
                if msg.type == 'note_on':
                    on.append(elapsed)
                elif msg.type == 'note_off':
                    off.append(elapsed)
            for t in on:
                self.assertAlmostEqual(t, .2, delta=.002)
            for t in off:
                self.assertAlmostEqual(t, .7, delta=.002)

    def test_missing_key_fallback(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True):
            root = Path(td)
            src = root / 'input.wav'
            src.write_bytes(b'fixture')
            audio = engine.AudioResult(120, [.2, .7], [1, 1], 1)
            with patch('engine.analyze_audio', return_value=audio), patch('engine.librosa.get_duration', return_value=1):
                report = engine.build_song(src, root / 'refs', root / 'out', direct_audio=True)
            self.assertEqual(report['mode'], 'fallback')
            self.assertEqual(report['generation']['chunks'][0]['reason'], 'missing_api_key')
            self.assertFalse(report['generation']['chunks'][0]['audio_submitted'])

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'})
    def test_api_failure(self):
        with patch('engine.librosa.load', return_value=(np.zeros(22050), 22050)), patch('openai.OpenAI') as client:
            client.return_value.chat.completions.create.side_effect = RuntimeError('private')
            notes, outcome = engine.direct_audio_chart(Path('fixture'))
        self.assertIsNone(notes)
        self.assertEqual(outcome['error_type'], 'RuntimeError')
        self.assertNotIn('private', json.dumps(outcome))
