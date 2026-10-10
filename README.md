# Multivac Hero (24-hour MVP)

Generate an approximate **Expert five-fret Clone Hero chart** from audio. Python detects onsets and tempo; a simple rhythm-based retriever finds similar patterns from human-authored reference `notes.mid` files. An optional LLM chooses frets based on retrieved examples. Python writes the MIDI timing, validates note values, and packages a Clone Hero song ZIP.

**This is a rough automatic chart generator, not accurate instrument transcription.** The default mode uses only reference patterns; to claim LLM-powered RAG in a demo, enable `--llm` with a working API key and check the generation log. API failures deliberately fall back to a rule-based chart. The generated MIDI has **Expert guitar only**. Tempo is estimated and fixed; audio onset timing and fret quality may be imperfect. Very long songs may take time and incur API costs.

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
