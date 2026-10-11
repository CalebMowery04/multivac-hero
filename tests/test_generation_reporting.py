import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mido
import engine


class GenerationReportingTests(unittest.TestCase):
    def setUp(self):
        self.audio = engine.AudioResult(120, [i * .5 for i in range(13)], [1] * 13, 7)

    def completion(self, frets):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'frets': frets})))])

    def test_baseline_does_not_call_llm(self):
        with patch('engine.llm_frets') as llm:
            frets, _, report = engine.generate_frets(self.audio, [])
        llm.assert_not_called()
        self.assertEqual(len(frets), 13)
        self.assertEqual(report['mode'], 'baseline')
        self.assertEqual(report['baseline_chunks'], 2)

    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            frets, _, report = engine.generate_frets(self.audio, [], True)
        self.assertEqual(frets, engine.generate_frets(self.audio, [])[0])
        self.assertEqual(report['mode'], 'fallback')
        self.assertEqual(report['fallback_chunks'], 2)
        self.assertEqual(report['chunks'][0]['reason'], 'missing_api_key')

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'})
    def test_success(self):
        with patch('openai.OpenAI') as client:
            client.return_value.chat.completions.create.side_effect = [self.completion([4] * 12), self.completion([2])]
            frets, _, report = engine.generate_frets(self.audio, [], True)
        self.assertEqual(frets, [4] * 12 + [2])
        self.assertEqual(report['mode'], 'llm')
        self.assertEqual(report['llm_chunks'], 2)
        self.assertEqual(report['fallback_chunks'], 0)

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'})
    def test_mixed_api_failure_redacts_error_text(self):
        with patch('openai.OpenAI') as client:
            client.return_value.chat.completions.create.side_effect = [self.completion([3] * 12), RuntimeError('sensitive response')]
            frets, _, report = engine.generate_frets(self.audio, [], True)
        self.assertEqual(len(frets), 13)
        self.assertEqual(report['mode'], 'mixed')
        self.assertEqual((report['llm_chunks'], report['fallback_chunks']), (1, 1))
        self.assertEqual(report['chunks'][1]['start_event'], 12)
        self.assertEqual(report['chunks'][1]['error_type'], 'RuntimeError')
        self.assertNotIn('sensitive response', json.dumps(report))

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'})
    def test_invalid_response_falls_back(self):
        for invalid in ([5], [True], [], 'bad'):
            with self.subTest(invalid=invalid), patch('openai.OpenAI') as client:
                client.return_value.chat.completions.create.return_value = self.completion(invalid)
                frets, outcome = engine.llm_frets([0], 120, [], 'test', [])
                self.assertEqual(frets, [0])
                self.assertEqual(outcome['method'], 'fallback')
                self.assertEqual(outcome['error_type'], 'ValueError')

    def test_report_is_packaged_with_valid_midi(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'input.wav'
            source.write_bytes(b'fixture')
            with patch('engine.analyze_audio', return_value=self.audio), patch('engine.librosa.get_duration', return_value=7), patch.dict(os.environ, {}, clear=True):
                report = engine.build_song(source, root / 'refs', root / 'output', use_llm=True)
            packaged = json.loads((Path(report['folder']) / 'generation_report.json').read_text())
            self.assertEqual(packaged['generation'], report['generation'])
            self.assertEqual(packaged['mode'], 'fallback')
            midi = mido.MidiFile(Path(report['folder']) / 'notes.mid')
            notes = [m for t in midi.tracks for m in t if m.type == 'note_on' and m.velocity]
            self.assertEqual(len(notes), 13)
            self.assertTrue(all(96 <= m.note <= 100 for m in notes))
            with zipfile.ZipFile(report['zip_path']) as archive:
                self.assertTrue(any(n.endswith('/generation_report.json') for n in archive.namelist()))


if __name__ == '__main__':
    unittest.main()
