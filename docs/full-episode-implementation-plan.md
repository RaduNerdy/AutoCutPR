# Full Episode automation: research and implementation handoff

Date: 2026-09-24
Status: implementation authorized and added on 2026-09-24. Live Premiere tests were
subsequently deferred by the user. See `full-episode.md` for setup and validation status.
## 1. Resume instructions

Read this document before doing further design work. Do not repeat the product
questions already settled below.
The original research session changed documentation only. The later implementation
adds isolated engine/panel/bridge modules and a separate video-analysis environment.
No live Premiere timeline has been modified or rendered during implementation.

When implementation is requested, first inspect current git status and preserve
intervening work. Start with the compatibility experiments in section 11. Do not
claim the complete feature works on the strength of documentation or mocked tests.

## 2. Confirmed requirements

- Separate **Full Episode** tab, outside the existing numbered Source/Find/Review/
  Build/Export/Trailer workflow. Existing steps 1–6 remain available and unchanged.
- Two show profiles: **Ce ai în plan (CPI)** and **Arena Ideilor (AIP)**.
- User selects prim-plan media from Premiere's Project panel. Its timeline copies
  may have head/tail trims and harmless razor cuts made for reels.
- User manually places and synchronizes general-camera footage on **V2**, above
  prim-plan on **V1**. Do not implement automatic audio synchronization.
- General camera appears for **10 seconds roughly every five minutes**, scheduled
  against timeline time rather than the recording's original timecode.
- Apply **Lumetri Basic Correction Saturation 114** to general-camera sections in
  both shows. AIP additionally exposes a **Blacks** slider, default 0.
- Separate host animation: once per qualifying speaking turn, requiring an
  uninterrupted host shot and sufficient time for the complete asset to play.
- **Paralela 45 is a different asset**, available only in CPI. **Four placements
  total**, not four plus the opening placement. First at the sponsor mention in
  the first two minutes, e.g. “susținut de Paralela 45”. User says host is on screen
  during that opening mention. Three further placements distributed later.
- For Paralela 45, host visibility is sufficient; speaking is not required.
- CPI opening Like & Subscribe begins immediately after opening Paralela 45 ends.
  AIP opening Like & Subscribe begins around 30 seconds.
- Closing Like & Subscribe finishes 10 seconds before the timeline ends, both shows.
- Both Like & Subscribe instances use **50% scale**.
- Individual overlay toggles and an **All overlays off** option. General-camera
  placement and color controls are independent of those switches.
- Review proposed placements, then build a duplicate sequence. Original synchronized
  sequence stays intact. This approach was included in the accepted plan.

## 3. Research findings and confidence

### Existing project evidence

The CEP panel uses Chromium 99 and an ES3 ExtendScript host bridge. Keep the current
stack: no UXP migration in this feature. Respect DESIGN.md's CSS compatibility rules.
The Adobe UXP API is not interchangeable with the existing ExtendScript objects.

Reusable code in `panel/jsx/autocut.jsx`:

- `ACP_getSelection`, `ACP_findItemByPath`, `ACP_importAndFind`: item lookup/import.
- `ACP_ensureTracks`: existing QE-based video/audio track addition.
- `ACP_setMotion`: existing Motion Scale/Position property access.
- `ACP_applyAutoReframe`: existing QE effect discovery/addition pattern.
- `ACP_cutOnTimelineObj`: existing QE per-track razor pattern.

Do **not** reuse these unchanged for Full Episode:

- `ACP_detectOffset` returns one match/offset, insufficient for piecewise timeline mapping.
- `ACP_seqFps` rounds to two decimals and the current timecode helper rounds nominal
  FPS; new long-form scheduling needs exact ticks/frame, especially at 29.97.
- `ACP_placeLogo` changes project-item in/out and uses frame fitting; it can affect
  shared project-item state and does not define exact native-size 50% scaling.
- `ACP_buildReels` intentionally razors a master and creates 9:16 sequences; Full
  Episode must have an independent builder preserving the original format.
- Old transcript/speaker caches are keyed by filename stem; new cache keys must
  identify the full source file to avoid same-name collisions.
- `HOST_VOICE` is global. Full Episode should use an editor-confirmed speaker label
  per episode, not overwrite the global voiceprint when switching shows.

### External research

Adobe's CEP sample demonstrates project selection, subclips, source/timeline Time
objects, sequence subsequences, and component parameter access. It establishes useful
patterns, not a guarantee of exact Lumetri values or QE behavior in this installation.
[Adobe sample](https://raw.githubusercontent.com/Adobe-CEP/Samples/master/PProPanel/jsx/PPRO/Premiere.jsx)

The community-maintained ExtendScript reference documents `Sequence.clone()` as
returning a Boolean, not the new sequence object. Identify a clone by comparing
sequence IDs before/after, not by assuming the active sequence or a guessed name.
It also distinguishes timeline start/end from source in/out, documents writable
TrackItem timing/disabled state, and defines Time ticks. Treat this as an API map to
verify against the installed application, not primary proof of runtime compatibility.
[Sequence](https://ppro-scripting.docsforadobe.dev/sequence/sequence/),
[TrackItem](https://ppro-scripting.docsforadobe.dev/item/trackitem/),
[Time](https://ppro-scripting.docsforadobe.dev/other/time/)

Adobe documents Saturation and Blacks as Basic Correction controls. It does not
establish their ExtendScript property indices or numeric conversion. Verify these
on a scratch sequence; never equate an arbitrary FFmpeg saturation/brightness filter
with Lumetri's displayed values.
[Adobe Basic Correction](https://helpx.adobe.com/dk/premiere/desktop/correct-color/color-correction-fundamentals/basic-color-correction-options.html)

PySceneDetect's AdaptiveDetector uses rolling comparisons of frame changes; its
documented purpose fits finding shot boundaries. Detection thresholds remain
empirical and must be tested on this podcast footage.
[Detector documentation](https://www.scenedetect.com/docs/latest/api/detectors.html)

OpenCV provides feature matching and geometric verification. Use those tools to
match editor-selected **background/framing regions** across shots. This is a proposed
application of the tools, not evidence of podcast-specific accuracy. Do not implement
face embeddings or identity recognition from portrait photos.
[Feature matching](https://docs.opencv.org/4.13.0/dc/dc3/tutorial_py_matcher.html),
[Geometric verification](https://docs.opencv.org/4.13.0/d1/de0/tutorial_py_feature_homography.html)

ffprobe can return stream metadata in JSON. Use it for codec, dimensions, duration,
frame-rate/timebase, audio-stream presence and pixel-format inspection. Metadata
alone does not prove useful transparency: also inspect decoded alpha and a composite.
[ffprobe](https://ffmpeg.org/ffprobe.html)

## 4. Architecture and file boundaries

Keep the extension's current entrypoint. Add isolated modules rather than expanding
the already-large reels orchestration functions.

Proposed new files during implementation:

- `panel/js/episode.js`: Full Episode state, controls, analysis progress, review/build.
- `panel/jsx/episode.jsx`: snapshot, capability inspection, clone/build/verify bridge.
  Explicitly load this file from the existing host entrypoint via a resolved path.
- `engine/episode.py`: separate CLI accepting a snapshot/config JSON path; does not
  add episode-specific flags to the existing reels `run.py` argument parser.
- `engine/episode_timeline.py`: pure mapping, interval arithmetic and scheduling.
- `engine/episode_shots.py`: probe/decode, scene boundaries, background matching,
  thumbnails and analysis cache. Lazy imports for optional video-analysis packages.
- `engine/test_episode_timeline.py` and `engine/test_episode_analysis.py`: focused tests.

Small wiring edits to panel/index.html, panel/css/panel.css and main.js. Introduce a
top-level Reels / Full Episode selector. Keep `STEPS`, reached-state, and all current
rail handlers scoped to the reels section so they cannot hide or navigate episode
sections. Reuse design tokens; no new design system or npm frontend build.

Full Episode uses Python/FFmpeg locally; it does not require new GPT requests. Reuse
the existing transcription and diarization functions through adapters with an isolated
cache directory (in separate worker processes); don't alter their existing defaults.
Only run speech analysis for enabled features that need it. Cache the manual host
speaker selection per episode and confirm it through sample playback.

## 5. Snapshot and time mapping contract

`ACP_episodeSnapshot` captures project identity, sequenceID, name, timebase, display
start, dimensions, V1/V2 clip records, audio invariants and existing upper-track coverage.
Each clip record includes projectItem node ID/path, track/index identity, source and
timeline in/out ticks, speed, reverse/disabled flags, and relevant transition/effect state.
Only ordinary file-backed, constant 1x clips are supported in v1. Refuse nests,
multicam, reverse/time-remapping or uninspectable effects that change visibility;
explain the offending clip. Do not silently approximate their mappings.

Store tick values as decimal strings across the JS/Python bridge. Calculate with
Python integers; use Premiere's `timebase` (ticks per sequence frame). Do not round
FPS to two decimals. All schedule intervals are half-open [start, end).

For a 1x clip: source(t) = sourceIn + (t - timelineStart).
For source words/shots: timeline(s) = timelineStart + (s - sourceIn), clipped to that
instance's visible range. Map V1 and V2 independently; never infer their offset from
their filenames or assume identical source in-points.

Merge razor fragments for analysis only when source identity, source continuity,
timeline continuity, speed and visual treatment agree. Preserve the actual edit
records for the build. A razor boundary is not itself an optical scene cut.

Use V1's selected episode coverage as the default episode interval; display start/end
for review. V2 overhang or stray upper-track media must not extend the episode length.
Periodic targets are sequence-relative 300s, 600s, etc. within that interval. Closing
graphics use the reviewed episode end. Display zeroPoint is not a source offset.

Reject stale plans: immediately before building, resnapshot and compare the relevant
timeline/config/source fingerprint. If changed, require reanalysis rather than applying
old timestamps. Source file keys include canonical path, size and high-resolution mtime;
settings and analysis algorithm versions also enter the cache fingerprint.

## 6. Video analysis without random sampling

Stage A: probe source files, restrict decoding to used source ranges, and scan at every
source frame at reduced spatial resolution (default analysis width 640, preserving
aspect ratio). Use AdaptiveDetector initially with documented defaults. Record frame
timestamps, not just frame ordinal / rounded FPS. Add a conservative transition guard;
do not treat one sample every few seconds as proof there was no intervening cut.

Stage B: present representative scene thumbnails. Editor labels host-camera view(s)
and selects fixed background/framing regions that exclude people's faces/bodies.
Use ORB descriptors, Hamming-distance matching and geometric inlier checks on those
regions to propose the same view in other shots. Check beginning, middle and end of
each proposed interval and more densely when frame changes suggest uncertainty.
Allow multiple references for zoom/crop variants; ambiguous shots require a manual
label. If scenery cannot distinguish the two cameras, manual shot labels are the
supported fallback. A standalone portrait is not used for biometric identification.

Stage C: intersect labelled host-shot intervals with visible V1 ranges. Exclude
transitions, covering upper-track clips and scheduled general-camera windows. Keep
a default 0.25s boundary margin for automatic placement. Visibility classification
is a proposal: it cannot guarantee the host never leaves the frame. Review playback
of complete selected intervals is part of acceptance.

For the host animation, also intersect with confirmed host-speaking turns. Allow
natural same-speaker pauses up to 0.5s, but never merge across a guest-labelled turn.
At most one instance per merged host turn; a camera switch cannot create additional
instances in the same turn. Require actual full asset duration, not a hard-coded 8s.

Reuse source-level shot analysis after timeline edits; regenerate only the mapping
and schedule when trims/sync change. Show processed duration and allow cancellation
between chunks. Do not contend with the existing GPU transcription worker; run stages
sequentially, with CPU video analysis as the initial default.

## 7. Deterministic scheduler

Schedule enabled graphics first, then general-camera inserts. Proposed defaults below
resolve ordinary details without another round of questions; expose results for review.

### Fixed graphics

Normalize Romanian diacritics, punctuation and number forms in transcript words, retaining
word indices. Search only words mapped into the first 120 seconds of the episode for
Paralela plus 45 / patruzeci și cinci, with support-language context when available.
Start at the first word of the matched sponsor clause (e.g. “susținut”), not an arbitrary
earlier timestamp. Show the matching text. If absent/ambiguous or the whole animation
doesn't fit, mark the placement unresolved and allow a manual timestamp; do not invent it.

CPI first Like & Subscribe starts exactly when that sponsor animation ends. If sponsor
is disabled but Like & Subscribe is enabled, use 30s as the visible, editable default.
An enabled but unresolved sponsor must not silently select that fallback. AIP starts
at 30s. Closing start = episodeEnd - 10s - assetDuration. Short-episode conflicts are
reported, not resolved by truncating an animation. Disable both if their shared toggle
is off; review permits disabling either individual occurrence.

### Three later sponsor placements

Reserve the opening sponsor and both Like & Subscribe intervals first. For the remaining
three sponsor occurrences, use nominal targets at 25%, 50% and 75% of episode duration;
opening search region excluded. Find distinct uninterrupted host-shot windows long
enough for the whole asset. Rank by distance to targets, prefer separation, and break
ties by earlier time. Use a bounded candidate assignment rather than greedy selection
that consumes the only viable window for a later target. Target four total whenever
four valid intervals exist. Otherwise report N/4 with explicit missing occurrences.
Never reuse a shot solely to force the count.

### Other host graphics and general camera

Allocate one host animation in the earliest remaining valid interval of each host turn.
If a higher-priority graphic consumes the interval, shift within that turn or skip.
Do not stack graphics by default. User may reposition/disable proposed events but a
move must still pass the full-duration and no-collision checks.

For each 300-second target, find a full 10-second V2-covered window within +/-30s,
nearest the nominal time. Avoid all overlays in v1 to keep the rules predictable.
If none exists, skip and explain; never freeze, loop, extend beyond source coverage
or break an animation. Prefer earliest candidate on equal distance. Do not place
a partial final interval near the episode end.

Every event records ID/type/asset, timeline interval, mapped source interval(s),
reason, evidence, confidence/review state and enabled flag. Plan JSON also contains
warnings and unresolved requirements. Building an intentionally reduced sponsor
count requires an explicit review acknowledgement, not another chat permission prompt.

## 8. Premiere build strategy

1. Verify capability result, source/asset fingerprints and reviewed schedule.
2. Clone the source sequence; identify exactly one new sequence by ID difference.
   Name `<original> - Full Episode - <show> - <timestamp>`. Never modify the source.
3. Work on the duplicate's selected V2 only. Razor at scheduled general boundaries;
   re-enumerate the track after each structural batch because object indices change.
   Enable scheduled pieces and disable non-scheduled pieces. This preserves sync,
   clip Motion/effects and source links; do not rebuild V2 from bare project items.
   Confirm audio tracks were not razored, inserted, moved, muted or otherwise changed.
4. Apply color settings only to retained V2 pieces. V1 and overlays receive no grade.
5. Allocate dedicated overlay tracks above existing picture tracks. Use isolated
   video-only subclips for complete animation assets, avoiding source project-item
   in/out mutation and accidental audio insertion. Verify their timing and duration
   after insertion. Restore any temporary item/sequence state in finally blocks.
6. Set Like & Subscribe Motion Scale to 50% of native dimensions, centered by default;
   preserve animation's internal transparent padding. Host/sponsor use native 100%
   scale centered by default. Provide per-asset placement preview/position control
   so authored graphics can be adjusted without changing the scheduling code.
7. Set the duplicate's sequence In/Out to the reviewed episode interval. Disabled
   V2 overhang can still influence Premiere's full sequence extent, so report that
   interval explicitly and use Sequence In/Out for a verification render. Do not
   call the reels exporter (it exports entire sequences) or silently introduce a new
   full-episode exporter. Preserve all original audio even if it extends beyond the
   reviewed interval; the duplicate's In/Out defines the intended delivery range.
8. Snapshot result and verify all invariants. Restore original active sequence on
   failure; keep a clearly named INCOMPLETE duplicate with the failure report instead
   of claiming success. Never automatically delete a user sequence.

The initial version places overlay video only and leaves original audio untouched.
Assets with sound are flagged in asset preview; adding overlay audio is a future
explicit option, not an accidental result of video insertion.

QE razor and effect access are compatibility-sensitive. Keep them inside one adapter,
not spread through Python/UI logic. Do not introduce a UXP or FCPXML fallback silently;
if the required capability fails, stop that build with a concrete diagnostic.

## 9. Color, transparency and asset checks

Use existing clip-level Lumetri if exactly one compatible, non-animated instance is
present; change only Basic Correction Saturation and (AIP) Blacks. If absent, add one
through the verified adapter. Multiple or animated instances require the editor to
select/normalize the intended grade; don't stack another effect and call it 114.
CPI leaves existing Blacks unchanged. AIP's slider writes an absolute Blacks value,
default 0, with the UI range verified against the installed Lumetri control.

Enumerate component identifiers and parameters on a scratch clip. Distinguish Basic
Correction Saturation from Creative Saturation. Record before/after raw values while
the displayed controls are set to known values; verify scripted writes and readback.
Do not hard-code a property index from old internet examples. Reject unknown mappings.

Probe every enabled asset. Check decoded alpha over representative frames and render
a checkerboard preview for editor confirmation. Opaque MP4 does not become transparent
by renaming or transcoding. Require a suitable alpha asset or an explicitly configured
keying workflow; v1 does not guess a black-background key that might remove artwork.
Use frame-aligned duration sufficient to show every asset frame; account for differing
asset and sequence frame rates, then verify the placed clip duration in Premiere.

## 10. UI, persistence and bridge interfaces

Full Episode sections: Show and timeline; General camera/color; Optional graphics;
Host shot/speaker labels; Analyze/progress; Placement review; Build result.

Proposed bridge calls (JSON inputs/outputs, prefixed ACP_episode):

- `Snapshot`: read-only timeline inventory and relevant fingerprint inputs.
- `Capabilities`: read-only availability report; destructive probes are separate,
  implementation-time operations against a named scratch sequence only.
- `Seek`: sequenceID plus ticks for reviewing a candidate, without altering edits.
- `Build`: verified source sequenceID, fingerprint and reviewed plan-file path.
- `Verify`: duplicate sequenceID plus expected event/track invariants.

Keep large payloads in project-local JSON files; pass paths through evalScript.
Use a proper ES3-compatible JSON reader/writer for new endpoints, not hand-built
strings containing unchecked quotes or evaluation of arbitrary file contents.

Persist show asset/settings profiles under ignored `cache/full_episode/profiles.json`.
Per-job snapshot/analysis/plan/result live in `cache/full_episode/<job-id>/`. Face-free
background references and shot labels are episode-specific, even when profiles share
assets. Never reuse shot labels automatically for a new recording/set.

Disable build while analysis runs. Re-enable buttons on success, error and cancellation.
After reopening the panel, restore saved settings and reviewed plans only after a
fingerprint check. No changes to existing reels cache formats or presets.

## 11. Implementation stages and stop conditions

### Stage 0: compatibility proof, before the complete feature

Requires Premiere running, a saved disposable test project, representative V1/V2
footage and the actual three animation assets. These were not supplied/probed in this
research session. That does not prevent writing this plan.

- Verify clone result discovery and original sequence invariants.
- Split/disable only V2; compare all audio and V1 clip identities/timing before/after.
- Test video-only subclips with an asset that includes audio.
- Verify exact 50% native scale, transparency, durations and frame boundaries.
- Verify Lumetri displayed 114 and several Blacks values through scripting/readback.
- Test shot classification with host/guest samples from real footage. Reject automatic
  propagation if scenery/framing cannot distinguish them reliably; use manual labels.

Deliver a small capability report and reviewed sample output. If an API assumption
fails, fix the isolated adapter or report the exact blocker before implementing the UI
around a capability that doesn't work. Never run these probes in the user's source sequence.

### Stage 1: pure model and scheduler

Implement snapshots, integer time mapping, data contracts, deterministic placement and
unit tests with synthetic fixtures. No Premiere write required for this stage.

### Stage 2: general-camera build and color

Implement duplicate build, V2 segmentation/disabled state and color verification.
Verify with a short synced sequence first, then a long sequence.

### Stage 3: optional graphics and review

Add asset inspection, profile settings, fixed Like & Subscribe placements and sponsor
transcript matching. Introduce review controls and stale-plan protection.

### Stage 4: host-shot proposals

Add isolated optional dependencies (OpenCV headless and PySceneDetect; pin compatible
versions after testing in a separate environment, not blindly upgrading the working
Whisper/NumPy stack). Wire editor-labelled camera-view analysis and existing speaker
turns into the scheduler. Test both shows, individual toggles and all-overlays-off.

### Stage 5: end-to-end release check

Run complete jobs for CPI/AIP, verify duplicate invariants, play every chosen overlay,
and render selected windows around general-camera/overlay boundaries plus one complete
episode. Run existing OAuth tests and reels smoke checks. Document verified limitations,
then create another backup/checkpoint if requested; do not overwrite the known-good one.

## 12. Acceptance test matrix

- Sync/time: different recording starts; head/tail trims; nonzero sequence display
  timecode; contiguous razor marks; genuine source discontinuity; two files with same
  basename; 25 fps and 30000/1001 fps; V2 begins late/ends early; no complete 10s window.
- Sequence: original unchanged; duplicate distinguished by ID; no audio mutation;
  existing V2 framing/effects preserved; clips outside reviewed bounds not exported;
  occupied upper tracks; repeated build does not stack duplicates on a previous output.
- Sponsor: CPI exactly four when feasible; opening included in four; numeral/word and
  diacritic variations; source mention trimmed out; ambiguous/missing mention; insufficient
  qualifying views; no sponsor codepath in AIP even if an old CPI asset path is saved.
- Host animation: speaking plus labelled host view required; listener-only shot excluded;
  guest interruption blocks; natural pause allowed; short turn skipped; actual camera cut
  versus harmless razor distinguished; one instance per turn.
- Graphics: opening/closing rules; toggle combinations; sponsor-off CPI fallback; 50%
  native scale; source alpha missing; embedded audio never leaks in; full asset playback;
  duration conversion across frame rates; collision priority and manual edits revalidated.
- Reliability: cancellation/resume; corrupt cache; source change; timeline changed after
  review; unexpected API failure; no valid shot labels; no accidental source modification.

Completion means automated mapping/scheduling tests AND real Premiere checks pass.
Timing accuracy and visual detection quality are separate results: don't conflate a
valid JSON plan with a correctly identified host shot or a verified render.

## 13. Known uncertainties preserved for the next session

The product decisions are settled. Runtime validation still needs the actual installed
Premiere build and real assets: Lumetri parameter conversion, QE track/item behavior,
source codec/alpha, native graphic positioning, and camera-background distinguishability.
These are engineering validation tasks, not reasons to repeat the user's questionnaire.
Defaults introduced in this document (30-second search radius, muted overlay audio,
native scaling interpretation, pause tolerance and analysis width) should be visible
in the implementation notes and review UI where relevant, and adjusted from evidence.
