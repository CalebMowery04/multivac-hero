"""File-based Go/Python boundary; never mixes reports with library stdout."""
import argparse
import json
from pathlib import Path
from engine import build_song

parser = argparse.ArgumentParser()
parser.add_argument('request', type=Path)
args = parser.parse_args()
request = json.loads(args.request.read_text())
result = build_song(request['audio'], request['references'], request['output'],
                    request['title'], request['artist'], request['seconds'] or None,
                    request['mode'] == 'llm', request['model'],
                    direct_audio=request['mode'] == 'audio')
Path(request['result']).write_text(json.dumps(result), encoding='utf-8')
