# SSST — headphone voice and singing exercise

An adaptation of van der Mee et al., *The short Sing-a-Song Stress Test*
(2020), DOI: https://doi.org/10.1016/j.autneu.2019.102612.

The paper uses three neutral read-only conditions, a countdown speaking
practice, and a 60-second singing anticipation followed by 20 seconds of
singing. This implementation preserves the anticipation concept but adds
English read-aloud exercises, real pitch matching, a selected 60-second
music clip with karaoke, live lyric recognition and optional scripted
negative feedback. The countdown practice is left out. It is an adaptation,
not an exact replication of the validated protocol.

## Run

Select **Tests → SSST** in the workspace, or from the repository root:

```sh
python3 sw/gui/tests/run_test.py ssst --save sw/gui/outs/ssst.json
```

All parameters appear in the GUI form and `configs/defaults.hjson`; Load…
and Save as… use the shared HJSON configuration workflow. Instruction
screens show a lighter panel with a large (i) icon, count down in whole
seconds and are skipped with SPACE. SPACE starts the exercise and
subsequently skips the current section silently; ESC ends the run and
retains results.

## Points

The running total is always visible top-right (big and clear). After every
read-aloud phrase and tone trial a full-screen check or cross appears with
the points earned: a pass shows ✓ with positive points, a fail shows ✗ with
negative ones, and the next trial starts automatically afterwards. The
check/cross is shown only there, never repeated in the corner.

* **Reading aloud** — the phrase appears only when it is time to read it;
  there is no timer. While reading, each word turns green as it is
  recognized and stays red until it is heard. Points are anchored at a 50%
  word match: every percentage point above 50% adds 10 points, every point
  below deducts 10 (a perfect phrase earns +500, total silence −500, 50%
  is 0). A recognized pause after speaking finishes the trial and shows the
  verdict (`pause_time`).
* **Tone matching** — every millisecond inside the tolerance is +1 point,
  every millisecond outside is −1 point (a full 6 s window spans ±6,000).
  The sign of the result is the check/cross.
* **Singing** — each karaoke line is scored, as it ends, against the words
  recognized during it, with the same 50%-anchored rule as reading aloud;
  the line's points appear small to the right of the line and the total on
  top updates immediately. A live `LYRICS n%` indicator, a song progress bar
  and the feedback bars run during the song. The bars compare the voice with
  an **approximate** pitch contour extracted from the recording (green when
  within ~a quarter tone of it) — they exist to add pressure and give
  feedback, and are never a measurement.
* The end screen reports the reading/pitch/singing breakdown and the total,
  and offers SPACE to run again or ESC to quit.

## Sequence

1. Instructions (i): read aloud the phrase that will appear on screen.
2. Each trial: a 5 s countdown (the phrase is not shown yet), then the
   phrase appears and is read aloud — words turn green as they are
   recognized. A recognized pause ends the trial — there is no timeout — and
   a full-screen check/cross with the points earned is shown before the next
   countdown starts.
3. Instructions (i): hum each reference tone back and match its pitch.
4. Listen to each reference tone, then hum/sing it back. The bar shows a
   yellow target, a tolerance zone, and live measured voice position. Each
   tone trial also ends with the full-screen check/cross and its points.
5. Quiet rest, followed by hidden **number selection**. Only numbers appear;
   the participant has not yet been told the next exercise involves singing.
6. Instructions (i): the song is revealed, then a 10 s anticipation
   countdown.
7. Play the supplied clip: a short preparation with a progress bar, then the
   singing cue, the current lyric line with several upcoming lines in faded
   gray, per-line scoring to the right of the line, live lyric matching,
   pitch feedback bars and optional negative feedback.
8. Quiet recovery and end screen (run again or quit). If scripted feedback
   was used, the end screen explains that it was not a real assessment of
   singing ability.

Selecting a named song in the operator menu forces it and bypasses number
selection. The song list is filename-driven: any clip `0`–`9` in
[songs/README.md](songs/README.md) becomes that number's choice, with title
and artist taken from the filename. All ten numbers are offered to the
participant; a missing clip reports a setup error (ENTER retries, SPACE
skips the song to recovery). Full tracks are supported through an optional
`x_segment.txt` excerpt start. Clip and lyric files are read when chosen.

## Audio and measurement

* Select headphones as the **system default output**. `mic_device` controls
  input only. Playback uses `ffplay` from ffmpeg; the song contour feedback
  decodes the excerpt with `ffmpeg`. No speech is synthesized: every
  instruction is shown on screen.
* Microphone capture uses `sounddevice`; pitch processing uses NumPy.
  Speech uses the existing shared English Vosk model (auto-downloaded if
  absent). Optional dependencies load on a worker, not during GUI discovery.
* One input stream handles reading, tone matching and singing (recognition
  and pitch together in singing mode). Phase generation tokens discard late
  recognition/audio from previous prompts; no raw voice audio is saved.
* Speech accuracy is word edit similarity, not a language proficiency score.
  Silence scores zero when recognition is available; unavailable recognition
  and skipped/aborted trials are unscored, not fabricated successes.
* Pitch is normalized autocorrelation (65–900 Hz) with RMS/periodicity
  rejection. Octave errors count as errors. The results include voiced-frame
  counts, milliseconds inside/outside the tolerance, median absolute error
  in cents, and timestamped frequency samples. Trials without the reference
  tone remain unscored.
* Scripted singing feedback is separately tagged `scripted: true`; it is
  never included in genuine speech/pitch scores. No singing-accuracy model
  is claimed. Feedback begins only after the singing cue plus feedback_delay.
* Missing/failed song playback stops progression on a setup screen. ENTER
  retries from singing instructions; SPACE skips the song to recovery. A
  short clip is an error when it ends more than a second before test_time.
  Microphone/recognition failures are displayed and logged.

## Events and results

Yellow `SSST <section> start/end` boundaries identify instructions, neutral
countdowns, read-aloud trials, verdicts, tone matching, rest, number
selection, singing instructions, anticipation, preparation, singing and
recovery. Run boundaries are `SSST start/end/aborted` (and `SSST restart`
when the end screen starts a fresh run). All obey the
workspace Start/stop visibility toggle. Stimulus onsets use `stimulus`;
real verdicts use green/red and carry the trial's `points`. Karaoke line
transitions, song selection, recognized pauses and scripted feedback are
also timestamped. SPACE skips and aborts are explicit in the event details.

`--save` preserves configuration, speech/pitch/singing trials with their
points, song metadata, feedback and the entire timeline. As with MIST, the
JSONL feed is echoed to the terminal and imported by the Tests device;
workspace recordings retain every marker. See [songs/README.md](songs/README.md)
for file names, excerpt starts and lyric timestamps.
