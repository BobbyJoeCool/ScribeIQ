# Changelog

All notable changes to ScribeIQ are recorded here. Versions follow
[Semantic Versioning](https://semver.org/); the version lives in
`frontend/package.json` and `scribeiq/__init__.py`.

## [1.0.1] - 2026-11-10

### Fixed

- **Your level bar stayed empty while recording.** The microphone arrives as
  16-bit integer audio, but the recorder only measured float audio, so your
  level always read 0 even though your voice was being recorded. The recorder
  now measures every audio format. Rebuild it with `recorder/build.sh`.
- **Transcripts came out in the wrong language.** Whisper guessed the language
  from the first 30 seconds of each track. A call track that opens with silence
  or room noise could be misread (for example as Norwegian), and the whole track
  was then transcribed in that language, with repeated nonsense phrases.

### Added

- `SCRIBEIQ_LANGUAGE` setting (default `en`) tells Whisper which language is
  spoken, for recorded calls, audio files, and the command line. Set it to
  `auto` to let Whisper guess, for example for audio that isn't in English.

## [1.0.0] - 2026-11-05

### Added

- Record from any app (or all system audio) alongside your microphone, as two
  synced tracks, using a native ScreenCaptureKit helper.
- React + FastAPI desktop UI, replacing the Gradio interface: record, transcribe
  files or URLs, and browse transcript history.
