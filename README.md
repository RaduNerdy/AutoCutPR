# AutoCutPR

AutoCutPR is a Windows-based Adobe Premiere Pro extension for editing long podcast and interview recordings into vertical reels, trailers, and prepared full episodes. It combines local transcription, speaker diarization, quote matching, and timeline automation with optional AI editorial suggestions.

## What it does

- **Reels:** match rough timecodes and quotes or discover candidate moments, review the results, remove dead air, reframe to vertical video, add branding and optional music, and export.
- **Trailers:** propose excerpts and their ordering for editorial review.
- **Full episodes:** schedule wide-camera inserts, color adjustments and animated graphics using manually synchronized camera tracks, then build a separate sequence. Live Premiere verification of this newer workflow is pending.
- **Local processing:** audio/video analysis and timeline editing happen locally. Optional model requests may send transcript excerpts to a remote AI provider.

## Forking and adapting the project

The Markdown files are included specifically to help developers and AI coding assistants understand, fork, maintain, and customize AutoCutPR:

| Document | Purpose |
| --- | --- |
| [PRODUCT.md](PRODUCT.md) | Product intent, users, workflows and editorial principles |
| [DESIGN.md](DESIGN.md) | UI design system and Premiere CEP compatibility |
| [Full Episode guide](docs/full-episode.md) | Setup, operation and limitations |
| [Implementation plan](docs/full-episode-implementation-plan.md) | Architecture, rationale and acceptance checks |

The podcast profiles are examples of the original editing workflow. Adapt their names, timing rules, prompts and graphics for your own productions. No client footage, animation assets, transcripts, credentials, or local caches are distributed. No separate software license has been selected yet.

## Install

Requirements: Windows, Adobe Premiere Pro with CEP extension support, Python 3.10+ (3.13 used in development), FFmpeg and ffprobe on PATH, and Node.js 20+ for OAuth. GPU acceleration is optional.

From a clone of this repository:

```powershell
python -m pip install faster-whisper rapidfuzz numpy scikit-learn sherpa-onnx pillow
powershell -ExecutionPolicy Bypass -File install.ps1
```

For GPU diarization, install the compatible sherpa-onnx CUDA build and NVIDIA runtime wheels for your system; CPU fallback is available. The installer enables CEP development mode and creates machine-local configuration if missing. Restart Premiere and open Window > Extensions > AutoCutPR. Run Diagnostics before the first reel build.

For Full Episode, also run:

```powershell
powershell -ExecutionPolicy Bypass -File setup-episode.ps1
```

### ChatGPT OAuth and GPT-6 Astra

Moment discovery, trailer suggestions, and music selection now default to
`gpt-6-astra` with **medium reasoning**, using your ChatGPT login through the
local [openai-oauth](https://github.com/EvanZhouDev/openai-oauth) proxy. No OpenAI
API key is required. These model requests run on OpenAI's servers; transcription,
speaker detection, matching, rendering, and Premiere editing still run locally.
Your account must have access to Astra and available ChatGPT/Codex usage.

From the project folder, run once in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\oauth.ps1 setup
```

This requires system Node.js 20+ and installs the locked `openai-oauth` 2.0.0
dependency into `oauth/node_modules`. It reuses a valid local Codex OAuth login
or opens browser login when needed. Credentials stay with the package's local
credential provider; do not put tokens in `.env` or the repository.

Set `AI_PROVIDER=openai_oauth` in `engine/.env` (also the default when absent).
The engine starts the proxy hidden on `127.0.0.1:10531` when an AI action needs it,
checks account model access, and reuses it across operations. Ordinary runs never
install or update npm packages. The proxy remains running until stopped or the
computer shuts down. Its managed runtime state lives in ignored `cache/oauth/`.

```powershell
.\oauth.ps1 status
.\oauth.ps1 start    # optional; normally automatic
.\oauth.ps1 stop     # stops only AutoCutPR's managed proxy
.\oauth.ps1 login    # interactive sign-in again when credentials expire
```

Discovery and trailer requests fail clearly if login, account access, or service
availability prevents a result. Music is optional: failed selection leaves a
reason and no automatic music bed. There is no automatic provider/model fallback.
The client waits for a completed response and validates JSON before using it;
partial streams never become cuts. Responses Lite and the proxy's SSE response
with a JSON content type are handled explicitly.

Troubleshooting:

- **Setup required:** rerun `oauth.ps1 setup`; Node must be the system executable,
  not Premiere's embedded runtime.
- **Login required/expired:** run `oauth.ps1 login` in an interactive terminal.
- **Astra unavailable:** the account model list does not include `gpt-6-astra`.
  Check account access; the app will not switch to another model.
- **Port 10531 occupied:** free the port. AutoCutPR does not kill unrelated services.
- **Startup timeout:** run `oauth.ps1 status`, check connectivity, then retry.
- **Usage limit:** wait for ChatGPT/Codex allowance to reset. Transient HTTP 429/5xx
  errors receive at most two retries; authentication errors are not retried.
- **Python cannot start:** set `python` in `panel/config.json` to the full path of
  your working interpreter if Premiere cannot find it on PATH.

To roll back explicitly, set `AI_PROVIDER=ollama` in `engine/.env`. Existing
`OLLAMA_API_KEY`, `OLLAMA_BASE_URL`, and `OLLAMA_MODEL=gemma4:31b-cloud` settings
remain supported. Local `.env` values take precedence over process environment
variables. OAuth always uses Astra/medium and ignores Ollama settings.

Run the isolated transport/feature regression tests with:

```powershell
python -m unittest discover -s engine -p test_llm.py -v
```

Requires: Python on PATH with `faster-whisper` and `rapidfuzz` installed.
GPU is used automatically when free (pip wheels `nvidia-cublas-cu12` + `nvidia-cudnn-cu12`
provide the CUDA libs); falls back to CPU (~25 min/episode instead of ~5).

Note: transcription runs in its own subprocess because releasing a CUDA whisper model
can crash the host process (exit `3221226505` / `0xC0000409`) *after* the transcript is
safely written. `transcribe.py` fsyncs the JSON then `os._exit(0)`s to skip that teardown,
and `run.py` continues if the transcript is complete regardless of the child's exit code.

## Editing workflow

1. Select the source media in Premiere and prepare the episode. Confirm the host speaker.
2. Paste a list of timecodes and approximate quotes, or ask the model to discover moments.
3. Review the matched quotes, durations and speaker breakdown. Adjust the selections.
4. Build vertical sequences and optionally add branding and music. Preview the first reel before batch building and exporting.
5. Use Trailer to get editorial suggestions for excerpts and ordering.

For full episodes, open the separate Full Episode tab, manually synchronize the wide camera on V2 above the close-up material on V1, configure graphics and color, analyze and review placements, and build a duplicate sequence. See the [full episode guide](docs/full-episode.md) for calibration and editing steps.

## How it works

The Python engine transcribes the source with faster-whisper and clusters speaker turns with sherpa-onnx. Both manual and AI discovery use approximate quotes as anchors for word-level matching. The engine enforces duration constraints, prevents overlaps and uses available speech information to filter candidates. ExtendScript connects the review decisions to Premiere sequences.

- `engine/`: transcription, matching, diarization, model client, video analysis and scheduling.
- `panel/`: CEP interface and ExtendScript timeline bridge.
- `oauth/`: pinned local OAuth proxy dependency.
- `docs/`: engineering documentation for maintainers and forks.

## Privacy and validation

Keep media, client lists, credentials and generated outputs outside Git. Local configuration and analysis caches are ignored. Optional AI features transmit relevant text to the chosen remote provider. The project does not distribute production footage or graphics.

The existing reels and OAuth workflow has been exercised on the original installation. Full Episode has automated scheduling and analysis fixture checks; live Premiere integration remains unverified.

```powershell
python -m unittest discover -s engine -p 'test_episode*.py'
python -m unittest discover -s engine -p 'test_llm.py'
```