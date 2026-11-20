# Changelog

All notable changes to ScribeIQ are recorded here. Versions follow
[Semantic Versioning](https://semver.org/); the version lives in
`frontend/package.json` and `scribeiq/__init__.py`.

## [1.0.2] - 2026-11-19

### Fixed

- **An unreadable audio file showed a wall of ffmpeg output.** Transcribing a
  file ffmpeg can't read (say, a renamed text file) failed with the full ffmpeg
  command line and left a temporary WAV behind. It now says which file couldn't
  be read and why, for example "Couldn't read bad.m4a as audio (Invalid data
  found when processing input).", and cleans up after itself.
- **Speakers ran together in saved Markdown transcripts.** In the `.md` copy,
  each speaker's name was glued onto the end of the previous speaker's lines,
  so Markdown viewers showed several speakers as one paragraph. Every turn and
  pause marker is now its own paragraph. Older `.md` files pick up the new
  layout the next time you rename a speaker in that transcript.

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
