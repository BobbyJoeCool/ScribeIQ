# Test Plan — ScribeIQ

**Issue:** #2 · **Status:** done · **Written:** 2026-11-18

## Where things stand

- **Python unit/API tests:** good. `tests/test_scribeiq.py` covers track merging, pause markers, speaker naming and renaming, the recording state machine, the recorder protocol (with `tests/fake_recorder.py` speaking the helper's stdin/stdout protocol), file naming and language options. `tests/test_api.py` covers the FastAPI endpoints. 51 tests in all, run with `python -m unittest discover tests`.
- **Front end:** partial. `frontend/src/components/components.test.tsx` (Vitest + Testing Library) covers `RecordControls`, `toBlocks` and `TranscriptView`. `FileTab`, `HistoryTab`, `RecordTab`, `LevelMeter`, `Progress`, `api.ts`, `live.tsx` and `toasts.tsx` have no tests.
- **Untested Python:** `scribeiq/transcripts.py` (170 lines) has no direct tests; `pipeline.py` (334 lines) is only reached through stubs; `transcribe.py` (the command line) has none.
- **Swift helper** (`recorder/ScribeRecorder.swift`): no automated tests; checked by hand.
- **End-to-end:** none.

## Goal

Unit tests for every module and E2E tests for every user-facing flow, per `CLAUDE.md`, without ever recording real audio or loading real models.

## Tooling

| Layer | Tool | Location | Command |
|---|---|---|---|
| Python | `unittest` (existing) | `tests/` | `python -m unittest discover tests` |
| Front-end unit | Vitest + Testing Library (existing) | `frontend/src/**/*.test.tsx` | `cd frontend && npm test` |
| E2E | Playwright (Node, in `frontend/`) | `frontend/e2e/` | `cd frontend && npm run test:e2e` |

Playwright lives in `frontend/` because that's where the Node tooling already is. Claude asks Bob before installing it.

**How E2E runs the app:** Playwright's `webServer` starts the FastAPI app in a test mode that swaps in the fake recorder and a stubbed transcription pipeline (the same seams `tests/test_api.py` uses) with a temp output folder, and serves the built front end. The test mode is enabled by an env var and refuses to start unless it is set explicitly, so it can't switch on by accident. The fake recorder's level reports drive the meters, and the stub returns a fixed two-speaker transcript.

## Unit tests to write

### Python

1. `transcripts.py`: list, load, rename speakers, delete and download; a missing or corrupt transcript file raises `TranscriptNotFound` or a clear error; names never collide.
2. `pipeline.py`: audio prep and formatting functions with small fixture WAVs and fake Whisper/pyannote results; `PipelineError` for unreadable audio; `.txt` and `.md` output matches golden files.
3. `transcribe.py`: argument parsing, and a run with the pipeline stubbed writes the expected files.
4. `config.py`: `.env` loading and defaults.
5. `api.py` gaps: the `/api/live` WebSocket (connect, receive level and state messages, disconnect mid-recording) and the catch-all 404 route.

### Front end

1. `api.ts`: each call's method, path and body; error responses become readable messages.
2. `live.tsx`: WebSocket messages update state; reconnects after a drop.
3. `toasts.tsx`: show, auto-dismiss, dismiss on click.
4. `RecordTab`: choose an app or system audio; record, pause, resume, stop, finish; shows the processing state and the error state.
5. `FileTab`: pick a file or paste a link; validation for an empty or unsupported input; shows job progress, then the transcript.
6. `HistoryTab`: lists transcripts newest first; empty state; opening one shows it.
7. `LevelMeter` and `Progress`: values clamp to 0–100 and render the right width.

### Swift helper

Out of scope for automation for now: it needs ScreenCaptureKit and screen-recording permission. The protocol it speaks is already pinned by the fake recorder tests. **Bob decides** later whether to add an XCTest target for the parts that don't touch ScreenCaptureKit (command parsing, output formatting).

## E2E tests to write

1. `record.spec.ts`: pick an app, record, see the timer and both level meters move, pause (pause marker), resume, stop, finish, and land on the transcript with speaker labels and the pause marker.
2. `record-errors.spec.ts`: the helper reports `ERROR` (fake set to fail) and the UI shows the message and lets the user retry; the chosen app isn't running.
3. `rename-speakers.spec.ts`: rename "Speaker 1" to a name and see it change everywhere; the change persists after reload.
4. `file-transcribe.spec.ts`: upload a fixture audio file, watch progress, read the transcript; download `.txt`, `.md` and `.json`.
5. `history.spec.ts`: transcripts from earlier tests appear; open one; delete one only after confirming.
6. `layout.spec.ts`: the window at its minimum supported size.

## Order of work

1. Claude writes the Python gaps (`transcripts.py`, `pipeline.py` formatting, `transcribe.py`, the WebSocket).
2. Claude writes the front-end unit tests.
3. Claude adds the API test mode (with a unit test proving it's off unless enabled).
4. Claude sets up Playwright and writes the E2E specs.
5. Claude updates the README's "Tested" row and `CLAUDE.md` with the E2E command.

## Done when

- `python -m unittest discover tests`, `cd frontend && npm test` and `cd frontend && npm run test:e2e` all pass.
- No test records real audio, loads a real model, or downloads anything.

## Result

Done on branch `issue-2-test-plan` (#2). Test counts went from 51 Python + 8 front-end + 0 E2E to **135 Python, 73 front-end and 15 E2E**, all passing.

| File | Covers |
|---|---|
| `tests/test_transcripts.py` | The transcript store: save, unique names, rename, list, load, legacy text, unsafe ids, download paths, delete to Trash |
| `tests/test_pipeline.py` | Names and URLs, the download cache (network stubbed), audio prep, speaker assignment, `.txt`/`.md`/plain output against `tests/golden/`, `transcribe_source` with Whisper and pyannote stubbed |
| `tests/test_cli.py` | `transcribe.py`: file mode, call mode, argument errors, clean exit on `PipelineError` |
| `tests/test_config.py` | `.env` parsing (real environment wins) and every default and override |
| `tests/test_api.py` (added to) | WebSocket state changes, disconnect mid-recording, WebSocket origin check, the catch-all 404 |
| `tests/test_e2e_server.py` | The E2E server refuses to start without `SCRIBEIQ_E2E=1`, and its stubs behave as the specs expect |
| `frontend/src/*.test.ts(x)`, `frontend/src/components/*.test.tsx` | `api.ts`, `live.tsx`, `toasts.tsx`, `RecordTab`, `FileTab`, `HistoryTab`, `LevelMeter`, `Progress` (shared fakes in `src/test-utils.tsx`) |
| `frontend/e2e/*.spec.ts` | The six specs listed above |

### Bugs the tests found

Fixed with Bob's go-ahead, in a separate PR (#3) released as v1.0.2. This work is built on top of that branch.

- **Unreadable audio** (`pipeline.prepare_audio`): a file ffmpeg can't read raised a raw `CalledProcessError` (the UI showed the whole ffmpeg command line) and left a temp WAV behind. It now deletes the temp file and raises `PipelineError`: "Couldn't read bad.m4a as audio (Invalid data found when processing input)."
- **Markdown output** (`pipeline.format_markdown`): a turn's text ran straight into the next `**Speaker**` line, so Markdown rendered both speakers in one paragraph, and lines ended in a stray space. Each heading, turn and marker is now its own paragraph. Saved `.md` files are rewritten when a transcript is next renamed; others keep the old layout.

### Where the build differs from this plan

- **Test mode** lives in `tests/e2e_server.py`, not in `app.py`/`api.py` (Bob's choice), so production code has no test branches.
- **History order and earlier tests:** E2E specs don't depend on each other. The E2E server saves two transcripts at startup, and specs that need their own create them through the API. Newest-first ordering is covered by the unit tests, because transcripts saved in the same second have no defined order.
- **No `.json` download:** the UI offers `.txt` and `.md` only, so `file-transcribe.spec.ts` checks those two and confirms there's no `.json` link.
- **App not running:** with `FAKE_RECORDER_LIST_GONE=1` the fake recorder also lists a "Gone App" that isn't running when recording starts. The E2E server sets this.
- **Picking a file in E2E** drops a browser-made `File` on the drop zone rather than using `setInputFiles`, so the suite needs no `@types/node` and covers drag-and-drop.
- **Playwright** is pinned at 1.64.1, the newest release at least two weeks old.

### Still open

- The Swift helper has no automated tests (**Bob decides** about an XCTest target).
- `app.py` (the pywebview launcher) has no tests.
- `tests/test_scribeiq.py` prints 8 `ResourceWarning`s for unclosed recorder pipes. These were there before this work.
