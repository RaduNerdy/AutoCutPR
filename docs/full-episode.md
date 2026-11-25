# Full Episode

Implementation added 2026-09-24. **Live Premiere testing was deferred at the user's
request. Treat the first duplicate as an integration check, not a delivery master.**
The existing reels engine, OAuth client, speech environment, and numbered workflow
are unchanged. The original working backup/checkpoint remains available.

## Setup

The new video dependencies are isolated under `.episode-venv`, ignored by Git.
They have been installed on this computer. On another installation, run:

```powershell
.\setup-episode.ps1 -Python python
```

FFmpeg and ffprobe must be on Premiere's inherited PATH. The existing speech Python
is read from `panel/config.json`; it must still have Whisper and diarization working.
Ordinary episode runs do not install packages. Close/reopen the AutoCutPR panel after
updating the extension; restart Premiere if its host script is cached.

## Editing workflow

1. Save the project. Open the sequence containing prim-plan on V1 and manually
   synchronized general footage on V2. Select the prim-plan file in the Project panel.
2. Open **Full Episode**, choose **Ce ai în plan** or **Arena Ideilor**, and read the
   timeline. The source sequence can contain harmless razor cuts and head/tail trims.
3. Choose graphics with useful alpha, or disable individual types. **All overlays off**
   leaves the general-camera setting independent. Unchecking general produces no V2
   inserts in the duplicate. Original V2 remains intact.
4. Analyze. Speech runs only when host/sponsor features need it. Video scanning checks
   every decoded frame at reduced spatial resolution, and caches source results.
5. Label host-camera shots and confirm the host speaker by listening to its sample.
   Optional background matching uses editor-drawn rectangles containing scenery only.
   It proposes similar views; it never labels a person from a portrait or automatically
   accepts a match. Manual shot labels work when scenery cannot distinguish cameras.
6. Create placements. Missing/ambiguous opening sponsor wording can be resolved with
   an explicit timeline-second cue in the first two minutes. Recreate placements after
   changing it. Graphics cannot be shortened to fit.
7. Play each full interval and mark it reviewed. Source playback in the panel bypasses
   the original continuously covering V2. Review source framing/color on the duplicate
   in Premiere. Enable/disable occurrences or move them within their eligible windows.
   Intentional sponsor shortfalls require the reduced-count checkbox.
8. Build a copy. The builder rechecks the source snapshot and media fingerprints,
   discovers the clone by sequence ID, splits V2 only, places video-only graphics on a
   dedicated upper track, and verifies timeline invariants. Failure leaves an
   **INCOMPLETE** copy and returns to the original. No user sequences are deleted.
9. Play the complete duplicate before delivery. Export using **Sequence In/Out**;
   disabled V2 overhang may still extend the sequence's overall extent. Do not use
   the vertical-reels exporter for this sequence.

### Rules implemented

| Feature | CPI | AIP |
|---|---|---|
| General camera | 10 seconds near each five-minute mark | Same |
| General grade | Saturation 114; preserve existing Blacks | Saturation 114; Blacks slider |
| Host animation | Once per qualifying host speaking turn and labelled uncut shot | Same |
| Paralela 45 | Four total: opening cue plus three spaced host views | Never |
| Opening Like & Subscribe | Immediately after opening sponsor; 30s if sponsors off | Around 30s |
| Closing Like & Subscribe | Finishes ten seconds before reviewed episode end | Same |
| Like & Subscribe size | 50% native Motion Scale | Same |

General targets use sequence-relative 300-second multiples with a ±30-second search.
They avoid graphics and covering upper-track clips. Host shots receive a 0.25-second
boundary guard; same-speaker pauses up to 0.5 seconds merge only without guest intrusion.
The full actual asset duration is rounded up to the sequence frame grid, never a fixed
eight-second guess. A Premiere duration mismatch stops the build.

## One-time Lumetri calibration

The live compatibility experiment was deferred, so no guessed raw Lumetri values or
property indices are shipped. Before the first build with general inserts:

1. Read your timeline, expand **One-time Premiere color calibration**, and create a copy.
2. On the copy's first V2 clip, add/enable one Lumetri Color effect. In **Basic Correction**,
   set Saturation **100**, Blacks **0**, and capture.
3. Set Saturation **114**, Blacks **+20**, and capture.
4. Keep Saturation **114**, set Blacks **−20**, and capture.

The panel identifies the uniquely changed controls, records Saturation 114 exactly,
and derives the Blacks conversion from symmetric samples. It refuses ambiguous,
nonlinear, animated, or changed control layouts. English effect labels are currently
required. Calibration belongs to the installed Premiere version; the copy is retained.
The first live check must confirm UI values and render output, including extreme Blacks
settings. Numeric readback alone is not a visual color-validation result.

## Supported inputs and clear failures

- V1: enabled, contiguous instances of the selected, ordinary, forward 1x file source.
  V2: ordinary 1x general-camera file clips; their individual sync is preserved.
- Retimed/reversed/nested/multicam sources, track transitions, animated picture effects,
  and unverified picture effects are refused. Clean those in a separate test sequence
  before using this first version. Static Motion/Opacity/Lumetri remain on V2.
- Alpha is checked on decoded representative frames. Opaque MP4 is refused rather than
  guessing a black-background key. Animation audio is omitted intentionally.
- Unknown/multiple Lumetri instances or unsupported localized controls stop grading.
- QE clip splitting/track addition, subclip duration conversion and native scale remain
  installation-sensitive. Their runtime checks can fail with a clear incomplete-copy
  result; they are not a substitute for the deferred live Premiere acceptance test.
- Background similarity is a proposal, not proof that the host stays in frame. Always
  play the whole accepted interval, especially when cameras have similar scenery.
- Cancel retains completed source/chunk caches. Resume a saved review only after its
  timeline matches. Changing media invalidates analysis even if its basename is the same.

## Files and state

- `engine/episode_timeline.py`: integer tick mapping, intervals, deterministic scheduler,
  sponsor text matching and reviewed-plan validation.
- `engine/episode_shots.py`: ffprobe/alpha inspection, scene decoding/cache, background matching.
- `engine/episode.py`: JSON job CLI, isolated speech subprocesses, cancellation and media validation.
- `panel/js/episode.js`: independent workflow, source/asset previews, labels, review and persistence.
- `panel/jsx/episode.jsx`: ES3 JSON parser, snapshot, calibration, cloning, build and verification.
- `cache/full_episode/profiles.json`: show-specific assets/settings and versioned calibration.
- `cache/full_episode/jobs/<id>/`: snapshot/config, analysis, reviewed plan and build result.
- `cache/full_episode/sources/`: canonical-path/size/mtime source caches. No global host voice changes.

## Validation status

- Python/JavaScript syntax checks passed.
- 18 new pure fixture tests passed (mapping, discontinuities, sponsor counts/text,
  host eligibility, collisions, frame alignment, stale media, alpha rejection, persistence).
- Video dependencies installed successfully in the separate environment.
- **Not performed:** live Premiere clone/razor/color/native-scale/alpha/render checks,
  real-episode shot accuracy, and Premiere reels/full-episode smoke tests. These were
  explicitly deferred by the user. No actual episode was analyzed or modified.

Run the new fixture suite later with:

```powershell
python -m unittest discover -s engine -p 'test_episode*.py'
```

The first live check should use a saved short synchronized copy and the real three assets,
then verify a long episode at the actual frame rate. Keep the known-good backup until
that visual check passes. Reels rollback requires no configuration changes: select Reels.

## API reference

The bridge follows the existing project's CEP/QE approach. Adobe's
[CEP Premiere sample](https://github.com/Adobe-CEP/Samples/blob/master/PProPanel/jsx/PPRO/Premiere.jsx)
and [Premiere typings](https://github.com/Adobe-CEP/Samples/blob/master/TypeScript/typings/PremierePro.11.1.2.d.ts)
provide host API patterns. Lumetri values are discovered locally instead of relying
on historical property indices. The earlier research and acceptance matrix are retained
in `full-episode-implementation-plan.md`.
