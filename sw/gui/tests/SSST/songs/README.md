# SSST song clips and karaoke

Put your prepared **60-second excerpts** here. Audio is played as supplied:
the application does not splice or download music. The **lead_in** setting
(default 8 seconds) is the preparation time before the singing cue, and
**test_time** is the clip duration if it differs from 60 seconds. By default,
**auto_lead_in** sets each song's singing cue to the *later* of lead_in and
the first nonempty lyric timestamp, so differing timings work with number
selection; turn off auto_lead_in to always use lead_in manually.

Use ONE audio file per number, with any of these extensions:
`.wav`, `.mp3`, `.ogg`, `.flac`, `.m4a`, `.aac`.

```
0_name_of_the_song--artist.mp3
1_another_song--artist.mp3
...
```

Numbers are filename-driven: **0–9**, ten possible choices. The displayed
title and artist always come from the filename (`_` → space, `--` between
them), so the numbering carries no fixed song list — use whichever numbers
you like for whichever songs you supply. Multiple files for one number are
reported as a setup error rather than arbitrarily choosing a recording.
Missing clips can be supplied while the task is open: press ENTER on its
audio setup screen to retry.

Full tracks are fine: add an optional `x_segment.txt` (next section) to
select the 60-second excerpt inside the file.

## Excerpt start: `0_segment.txt`, `1_segment.txt`, … (optional)

One timestamp marking where the 60-second excerpt begins inside the source
audio file. This matters when the clip is a **full track**:

```text
# comment lines are ignored
[00:31.20]
```

Playback then starts 31.2 s into the file and stops after test_time seconds
instead of running to the end of the song. A missing file simply means
"start at 0". Exactly one timestamp is expected; any other content is a
setup error.

## Lyrics: `0_lyrics.txt`, `1_lyrics.txt`, …

UTF-8 text, one timestamped line per karaoke line, using LRC-style
timestamps **relative to the excerpt start** (i.e. to the segment start,
not to the beginning of the full track):

```text
# 0.0 = the start of the played excerpt.
[00:00.00]
[00:08.50]First line to sing
[00:12.20]Second line to sing
[00:17.00]
[00:19.75]Next line after the gap
```

Empty timed lines clear the display. Multiple timestamps on a single line
are accepted for repeated lines. Alternatively, use seconds followed by a
TAB and the lyric text (`8.50<TAB>First line to sing`). A line already in
progress when the excerpt starts is shown from 0.0. Lines outside the
excerpt (before the segment start or beyond it by test_time) are ignored,
including the next-line preview. Metadata tags `[ti:...]`, `[ar:...]`,
`[al:...]`, `[by:...]` and `#` comments are ignored.

The current line is highlighted in yellow; the next line is previewed below.
This is **line-level karaoke**, not word-by-word highlighting. Lyrics are
optional; a malformed supplied lyrics file reports its line number.
The **lyrics_offset** setting shifts display timing (positive = later).

Playback uses ffplay and a monotonic clock referenced to player launch.
Output-device/player startup latency is not measured; adjust lyrics_offset
on the actual headphone setup. The preparation time before the singing cue
gives the player time to start before the first lyric. SPACE stops the
current section; skipping it seeks to the resolved cue time and starts singing.
