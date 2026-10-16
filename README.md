# Multivac Hero

Generate an approximate **Clone Hero Expert five-fret guitar chart** from a music
recording. An audio-capable model listens to the target song and uses an uploaded
human-authored chart/audio reference as context. Python validates the result and
packages a playable song ZIP. A browser viewer lets you preview the chart with audio.

## Requirements

- Python with a virtual environment and the packages in `requirements.txt`.
- Go 1.22 or later.
- Node.js 20.19+ or 22.12+ and npm for the Vite frontend.
- An OpenAI API key with access to `gpt-audio-1.5` for audio generation.
- Clone Hero for the final gameplay test.

MP3 and WAV are recommended for browser playback. Target uploads also accept OGG
and FLAC, but the current 3D viewer does not support FLAC.

## First-time setup

Run these commands from the project root:

```zsh
cd /Users/calebmowery/multivac-hero
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci --prefix web
npm run build --prefix web
```

Set the API key in the same terminal that will launch Go. In macOS zsh, this hides
the pasted key and keeps it out of the command itself:

```zsh
read -s "OPENAI_API_KEY?Paste your API key: "
export OPENAI_API_KEY
echo
```

Press Enter after pasting, then start the server:

```zsh
cd /Users/calebmowery/multivac-hero/server
go run .
```

Open **http://localhost:8080** in your browser. Leave that terminal running.
Press **Ctrl+C** to stop the server.

`.env.example` documents the variable name; the app does **not** automatically
load `.env` files. Never put a key in frontend code or commit it to Git.

## Running it again

If dependencies and the frontend build are already installed:

```zsh
cd /Users/calebmowery/multivac-hero/server
go run .
```

In a new terminal, export your API key again before starting Go. Activating the
Python virtual environment is optional for the Go server: it defaults to the
project's `.venv/bin/python`. Set `MULTIVAC_PYTHON` to an absolute executable path
to use a different Python environment.

## Generate a chart

1. Upload the **target song** you want charted and enter its title and artist.
2. Set **Target seconds to chart**. A positive value charts that many seconds from
   the beginning; `0` uses the whole recording, subject to request-size limits.
3. Choose **one reference song folder**, or reuse a saved reference.
4. Select which reference MP3 tracks should play together.
5. Select any combination of available reference guitar difficulties.
6. Set **Reference seconds to use**. The audio and MIDI are trimmed together from
   the beginning; `0` uses the full reference audio duration.
7. Click **Calculate estimate and preview**.
8. Review the reference mix, estimated token usage, and cost. Adjust reference
   duration, target duration, or difficulties and **Recalculate** as needed.
9. Click **Approve cost and generate** to make the API request, or **Do not send**
   to cancel preparation.
10. Preview the generated chart and download its ZIP.

Selecting reference difficulties changes the examples supplied to the model.
**Generated output remains Expert guitar only**, even if the reference selection
is Easy or Medium.

Without a reference, the app uses the older audio-only experiment: the target is
limited to the first 15 seconds, and generation starts directly without the
reference estimate/approval flow. That path can use a local fallback on failure.
Uploaded-reference generation instead reports a failure if the model does not
return a valid chart; it does not substitute baseline mapping.

## Reference folders

Example reference structure:

```text
Example Song/
├── notes.mid
├── song.mp3
├── guitar.mp3
├── rhythm.mp3
├── preview.mp3
├── song.ini
└── album.png
```

The importer needs **exactly one `.mid` file and at least one selected MP3**.
`preview.mp3` is ignored case-insensitively. Artwork, `.ini` files, and other
non-MP3/non-MIDI assets are ignored. Nested song folders and duplicate filenames
are not supported.

Choose tracks carefully: `song.mp3` might be a full mix or backing audio intended
to play with the other stems. The app cannot determine that automatically.
Selected tracks are mixed as mono 22.05 kHz WAV with their zero-time alignment
preserved. Short tracks are padded with silence; volume is reduced when necessary
to prevent clipping. WAV avoids another lossy MP3 encode.

Python reads every MIDI track and its timing. Guitar difficulty selection filters
`PART GUITAR` notes and per-difficulty markers; tempo events, shared markers,
lyrics, and other tracks remain. The selected time range uses the MIDI tempo map,
and notes held beyond its end receive note-offs at the boundary. A compact,
lossless dictionary encoding makes the retained events readable to the model
without repeating identical message fields.

Because `.ini` files are ignored, metadata offsets are **not applied**. Reference
MIDI and stems must already align at zero. No instrument separation is performed.

Selected files persist in ignored **`user_references/<id>/`** folders and appear in
the saved-reference dropdown. To change a saved reference's selected stems, upload
the original folder again with a different selection. The Go app does not use the
old bundled `references/` directory. References are request context, not training.

## Estimates and limits

Preparing or recalculating a reference request runs locally and makes no OpenAI
call. The review shows reference audio, target audio, MIDI text, prompt allowance,
output allowance, and estimated cost before approval. There is no spending-limit
field; approval confirms the displayed request.

The current estimator uses:

- 50 audio tokens per second as a conservative planning assumption.
- Local `o200k_base` token counting for the retained MIDI, plus 2,000 prompt tokens.
- An output allowance of 16,000 tokens.
- Configured rates of $32/M audio input, $2.50/M text input, and $10/M text output.

These are implementation assumptions, **not guaranteed billing amounts or a hard
spending cap**. Check rates before changing the model or relying on an estimate.
The tokenizer vocabulary is cached locally in ignored `.cache/tiktoken/`.

| Limit | Current behavior |
| --- | --- |
| Request upload | 500 MB total multipart upload |
| Reference files | Up to 32 selected files, each under 100 MB |
| Target duration field | 0–900 seconds; 0 means full recording |
| Reference duration field | 0–1,800 seconds; 0 means full reference |
| Selected audio duration | At most 30 minutes per reference mix or target clip |
| Estimated input context | 100,000-token local allowance |
| Concurrent jobs | One active worker; additional submissions receive a busy response |
| Job timeout | 30 minutes |

Oversized estimates stay on the review screen so you can shorten durations or
select fewer reference difficulties. Approval stays disabled until a recalculated
request fits. Reducing the reference duration reduces both audio and MIDI context.
No files are silently shortened beyond the settings shown in review. Submitted
model requests cannot be stopped at an exact dollar amount; audio calls have no
automatic retries.

## Results and chart playback

The downloaded ZIP contains a song folder with:

```text
Artist - Title/
├── notes.mid
├── song.mp3                # Original audio; extension follows the input
├── song.ini
├── generation_report.json
└── chart_preview.json
```

The full target recording is retained, even when only a prefix is charted.
Extract the folder into Clone Hero's Songs directory, rescan songs, and select
Expert guitar.

The web viewer uses **[Geomitron/chart-preview](https://github.com/Geomitron/chart-preview)**
(version 1.3.0) to render the actual exported MIDI as a 3D highway with synchronized
audio, seeking, volume, and fullscreen. It is a preview player, not a scoring game.
It requires WebGL and internet access for textures from `static.enchor.us`.
Safari uses static textures when animated textures are unsupported. Song audio
and chart files are fetched locally, not uploaded to the texture CDN.

The generation report is kept inside the ZIP instead of printed on the results
page. It records generation mode, reference settings, note counts, and API usage
when returned by the model. Passing validation means the chart is structurally
valid, not that it is musically accurate. See `THIRD_PARTY_NOTICES.md` for viewer
attribution.

## Architecture

```text
React interface → Go job API → Python worker → OpenAI audio model
                                   ↓
                         validated MIDI + song ZIP
                                   ↓
                         browser chart preview
```

| File or directory | Responsibility |
| --- | --- |
| `web/src/main.jsx` | Uploads, reference controls, estimates, job polling, results |
| `web/src/ChartPlayer.jsx` | React wrapper for the third-party 3D viewer |
| `web/src/style.css` | Frontend styling |
| `server/` | Local HTTP API, upload storage, approval, worker lifecycle, downloads |
| `worker.py` | File-based JSON boundary between Go and Python |
| `reference_context.py` | Stem mixing, MIDI filtering/trimming, encoding, estimates |
| `engine.py` | Audio analysis, model requests, chart validation, MIDI and ZIP export |
| `chart_preview.py` | Exported MIDI-to-JSON preview data retained for compatibility |
| `tests/` | Python tests |
| `app.py`, `main.py` | Older Streamlit interface and CLI |

Python still estimates an export tempo. In listen mode, that does not determine
note choices: the model receives audio and proposes its own event times, frets,
chords, and durations. Python validates finite ordered timing, fret ranges,
chords, clip boundaries, and same-lane overlap before writing MIDI pitches 96–100.

The API binds to `127.0.0.1`. It has no accounts and is intended for local use.
Job state, target uploads, previews, and result packages are temporary and disappear
on normal server shutdown. Saved references persist. The browser stores the latest
job ID to recover after a page refresh while the same server is running. An abrupt
process termination can leave temporary files in the system temp directory.

## Frontend development

Keep Go running. In a **second terminal**:

```zsh
cd /Users/calebmowery/multivac-hero/web
npm run dev
```

Open **http://localhost:5173** for automatic updates when saving React or CSS.
Vite proxies `/api` requests to Go on port 8080.

For the version served at port 8080, rebuild from the project root:

```zsh
cd /Users/calebmowery/multivac-hero
npm run build --prefix web
```

Then refresh the browser. If already inside `server/`, use
`npm run build --prefix ../web`. Restart Go after backend or Python changes.

To add the optional home image, put a PNG at **`web/public/resources/home.png`**.
Rebuild if using port 8080. The image area is hidden when no file exists.

## Tests

From the project root:

```zsh
.venv/bin/python -m unittest discover -s tests -v
```

For Go:

```zsh
cd /Users/calebmowery/multivac-hero/server
go test -race ./...
MULTIVAC_INTEGRATION=1 go test -race ./...
```

The optional integration test runs the real Python worker on synthetic audio,
without a paid API request. Python model tests mock API responses. MIDI timing,
reference trimming/filtering, approval state, file serving, and packaging are
covered; browser rendering and musical quality need manual testing. A successful
frontend build alone does not verify Safari playback or Clone Hero playability.

## Troubleshooting

- **`cannot find main module`:** run `go run .` inside `server/`, not the project root.
- **`address already in use`:** stop the older server with Ctrl+C in its terminal,
  then restart. Alternatively run `go run . -port 8081` and open that port; Vite's
  development proxy still targets 8080 unless you edit its configuration.
- **npm cannot find `package.json`:** from the root use `--prefix web`; from
  `server/` use `--prefix ../web`.
- **Frontend changes do not appear:** rebuild for port 8080, then refresh. Safari's
  Cmd+Option+R reloads without cached files. Use port 5173 during frontend development.
- **Missing API key:** export it before launching Go, in the same terminal. Restart
  Go after changing the environment; a separate Streamlit process cannot supply it.
- **Disabled prepare button:** select a target song, reference MP3s, and at least
  one available difficulty. The interface also disables submission during an active job.
- **Context too large:** shorten reference/target durations or choose fewer guitar
  difficulties, then recalculate. More money does not increase context capacity.
- **Viewer fails:** check WebGL, internet/CDN access, and browser audio support.
  Prefer MP3 or WAV. Expired jobs need a new generation.

## Older tools

These remain for experimentation but do not expose the React folder-upload and
estimate workflow:

```zsh
# From the project root, with .venv active
streamlit run app.py
python main.py path/to/song.mp3 --direct-audio --seconds 15
python main.py path/to/song.mp3 --references path/to/midi-library --seconds 30
```

The CLI's optional `--llm` mode maps locally detected attack times to frets using
retrieved MIDI patterns and a text model. It does not send audio to that model.
Its reference directory defaults to `references/`; that folder need not exist for
the current upload-based web app. Legacy paths can use deterministic fallback.

Keep API keys, uploaded music, unlicensed reference charts, and generated song
packages out of version control. Use recordings and charts you have permission
to process or share.
