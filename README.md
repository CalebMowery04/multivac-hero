# Multivac Hero (24-hour MVP)

Generate an approximate **Expert five-fret Clone Hero chart** from audio. Python detects onsets and tempo; a simple rhythm-based retriever finds similar patterns from human-authored reference `notes.mid` files. An optional LLM chooses frets based on retrieved examples. Python writes the MIDI timing, validates note values, and packages a Clone Hero song ZIP.

**This is a rough automatic chart generator, not accurate instrument transcription.** The default mode uses only reference patterns; to claim LLM-powered RAG in a demo, enable `--llm` with a working API key and check the generation report. API failures deliberately fall back to a rule-based chart. The generated MIDI has **Expert guitar only**. Tempo is estimated and fixed; audio onset timing and fret quality may be imperfect. Very long songs may take time and incur API costs.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py path/to/test.mp3 --title 'Test Song' --artist 'Test Artist' --seconds 30
```

The output ZIP is in `output/`. Unzip into your Clone Hero songs folder, scan for custom songs, and play Expert guitar.

## Web UI

```bash
streamlit run app.py
```

## Optional LLM

```bash
export OPENAI_API_KEY='your-api-key'
python main.py path/to/test.mp3 --llm --seconds 30
```

`--model` lets you change the model. LLM calls are made in chunks of 12 events and may cost money. The model must support Chat Completions JSON output. No API key is needed for the baseline. Only upload music and references you have rights to use. Do not commit API keys.

## Generation status

The UI, CLI output, and ZIP's `generation_report.json` report the actual mapping result:

- `baseline`: LLM disabled; reference-pattern mapping used.
- `llm`: every chunk received a valid LLM response.
- `fallback`: LLM requested, but no chunk received a valid response.
- `mixed`: some chunks succeeded and others used fallback.

The `generation` field includes chunk counts and per-chunk outcomes with zero-based
`start_event` and `event_count`. Fallback reasons include `missing_api_key`,
`missing_model`, and `api_or_validation_error`. Failures include the exception type,
without API response text. LLM success means the response passed fret validation;
it does not measure musical accuracy.

Run focused checks with `python -m unittest discover -s tests -v`.

## Input references

Place human-authored MIDI charts in `references/`. The example `la_grange.mid` contains the user-provided `PART GUITAR` track and is provided for local experimentation only. If publishing the repository, confirm you have distribution permission or exclude this file.

## Architecture

1. `analyze_audio()` — librosa onset times, tempo, basic density filtering.
2. `reference_patterns()` — parses MIDI `PART GUITAR`, Expert fret pitches 96–100; cuts into 12-note patterns.
3. `retrieve()` — ranks reference windows by timing differences.
4. `llm_frets()` — optionally requests 0–4 fret indices from the LLM, with strict validation and fallback.
5. `write_midi()` — saves `PART GUITAR` Expert as MIDI notes 96–100 with correct timed note-on/off messages.
6. `build_song()` — writes `song.ini`, copies source audio, and creates ZIP.

## Next steps

- Improve onset detection with isolated guitar audio / stem separation.
- Align music tempo grids, account for pickups and audio offsets.
- Improve repeated riff continuity, chords, HOPOs, sustains, and difficulty levels.
- Record a few before/after gameplay demos and measure onset error.

## Experimental direct audio charting

Enable **Let the model listen and create the chart (experimental)** in Streamlit,
or use `python main.py your-song.mp3 --direct-audio --seconds 15`.
This sends actual WAV audio to `gpt-audio-1.5` and requests text JSON with note
start times, durations, and chords. It does not supply Python onset timestamps
or reference examples to the model. Python validates and exports the result.
No new dependencies are required.

This initial experiment charts only the first 15 seconds (or the shorter selected
analysis duration), even when full-song analysis is selected. The ZIP retains the
full original recording, with no chart after the analyzed clip. Python still
estimates an export tempo; that tempo does not dictate model-proposed timestamps.
A rejected response or unavailable API uses deterministic onset/reference fallback,
reported explicitly. `generation.input_mode`, `generation.model`, per-chunk
`audio_submitted`, `charted_seconds`, and `exported_notes` document the result.
`audio_submitted` indicates an API call was attempted with audio, not confirmation
that the server processed it. Musical accuracy and live model behavior require
manual testing; automated tests mock API responses. Audio API charges apply.

## React frontend + Go API (local)

The new chart studio replaces Streamlit's direct function call with an HTTP job:
React → Go API → Python worker → song ZIP. Streamlit remains available.
The Go API uses only the standard library. React is built with Vite.

From the project root, build the frontend once:

```sh
npm ci --prefix web
npm run build --prefix web
```

In the terminal where `OPENAI_API_KEY` is exported, start the server:

```sh
cd server
go run .
```

Open **http://localhost:8080**. Go serves the built frontend and API together.
The backend inherits the API key from its terminal; a running Streamlit process
cannot pass its environment to a separately launched Go server. The UI shows a
warning if the backend has no key. `.env` files are not loaded automatically.
The default Python executable is `.venv/bin/python` in the project root;
set `MULTIVAC_PYTHON` to an absolute executable path to override it.
Go 1.22+ and a Node version supported by Vite 7 are needed.

For frontend development, keep Go running and in a second terminal use:

```sh
cd web
npm run dev
```

Open Vite's printed URL (normally http://localhost:5173). Its `/api` proxy forwards
to Go on port 8080. Rebuild the frontend to update the version served by Go.
Restart Go when backend or Python worker code changes.

API endpoints:

- `GET /api/health`: availability and whether an API key is configured (never its value).
- `POST /api/jobs`: multipart `audio`, `title`, `artist`, `seconds`, `mode`
  (`audio`, `llm`, or `baseline`), and `model`. Returns 202 with a job ID.
- `GET /api/jobs/{id}`: queued/running/completed/failed status and completed report.
- `GET /api/jobs/{id}/download`: completed ZIP.

Uploads are limited to 100 MB. One generation runs at a time; additional submissions
receive 409 rather than accumulating paid jobs. Jobs time out after 30 minutes.
The browser saves only the most recent job ID, allowing refresh recovery while Go
is running. Job records and generated files are temporary and disappear after a
normal server shutdown; an abrupt kill can leave a system temporary folder.
The original upload is removed after processing; the completed package contains
its audio until shutdown. This API binds to loopback and has no user accounts;
it is intended for local use, not public hosting. Generation failures are redacted
in the browser. For detailed Python troubleshooting use the existing CLI.

Validation:

```sh
cd server
go test -race ./...
MULTIVAC_INTEGRATION=1 go test -race ./...
```

The optional integration test runs the real Python worker on synthetic WAV audio
in baseline mode, including local reference retrieval, without an OpenAI request.

### Home screen image

Place a PNG at `web/public/resources/home.png` to display it above the form.
Without an image, the image area is hidden. Rebuild with
`npm run build --prefix web` and refresh if using the Go-served frontend;
Vite development mode only needs a refresh. See
`web/public/resources/README.md` for details.
