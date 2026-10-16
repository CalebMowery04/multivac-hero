"""File-based Go/Python boundary; never mixes reports with library stdout."""
import argparse
import json
from pathlib import Path
from engine import build_song

parser = argparse.ArgumentParser()
parser.add_argument('request', type=Path)
args = parser.parse_args()
request = json.loads(args.request.read_text())
if request.get('stage') == 'prepare':
    from reference_context import prepare, RequestSizeError
    try:
        result = prepare(request)
    except RequestSizeError as exc:
        result = {'preparation_error': str(exc), 'size_breakdown': exc.details}
    except (ValueError, KeyError, OSError, EOFError) as exc:
        # Local validation messages only; this stage never makes an API call.
        result = {'preparation_error': str(exc)}
    Path(request['result']).write_text(json.dumps(result), encoding='utf-8')
    raise SystemExit(0)
result = build_song(request['audio'], request['references'], request['output'],
                    request['title'], request['artist'], request['seconds'] or None,
                    request['mode'] == 'llm', request['model'],
                    direct_audio=request['mode'] == 'audio',
                    context_dir=Path(request['result']).parent if request.get('reference_folder') else None)
Path(request['result']).write_text(json.dumps(result), encoding='utf-8')
