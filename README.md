# h3-ws

Local **MiniMax-H3** video+audio generation on Apple Silicon — Web UI, CLI, and MCP over a single WebSocket server.

The inference engine is native **[h3.c](https://github.com/antirez/h3.c)** (Metal). The product shell follows [ltx-ws](https://github.com/audiohacking/ltx-ws): same generate / library / progress UX, new model options from the H3 CLI.

| Service | URL |
|---------|-----|
| Web UI | http://127.0.0.1:8765/ |
| WebSocket | ws://127.0.0.1:8765/ws |

See [ROADMAP.md](ROADMAP.md) for architecture and remaining phases. Agent notes: [AGENTS.md](AGENTS.md).

## Status

**P0–P4 plus Ref2VA UI are in tree:** scaffold, one-shot `h3` backend, WebSocket + library UI with H3 quality presets, FL2VA first/last-frame modes, autocontinue clip chains, ordered Ref2VA references, and documented canvas dropdowns. A generate still needs a built `h3` binary and MiniMax-H3 weights.

Not in this slice: resident interactive session (P5), CLI/MCP (P7).

## Requirements

- Apple Silicon Mac (Metal)
- Python 3.11+
- Node.js 18+ (to build `web/dist/`)
- Xcode command-line tools (`make` for h3.c)
- FFmpeg + FFprobe on `PATH`
- Disk / RAM — full residency is ~40 GB peak; use `--ssd-streaming` under ~64 GB

## Install

```bash
git clone --recurse-submodules https://github.com/audiohacking/h3-ws.git
cd h3-ws

# If you already cloned without submodules:
git submodule update --init --recursive

uv venv --python 3.12 --seed && source .venv/bin/activate
uv pip install -r requirements.txt

./scripts/build_h3.sh
python scripts/download_model.py

cd web && npm install && npm run build && cd ..
```

## Quick start

```bash
source .venv/bin/activate
python server.py
```

Open http://127.0.0.1:8765/ — default job is 512×512, 22 frames, **balanced** quality.

```bash
python server.py --width 512 --height 512 --num-frames 22 --quality balanced --ssd-streaming
```

## Engine

- Weights: [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) (`FL2VA/` + `Ref2VA/`)
- Output: 24 fps video + 32 kHz stereo audio
- Frame counts snap **up** to `5 + 17n`
- Canvas: documented sizes only in the UI (256×256 preview … 768×1344); width/height multiples of 32, max 768×1344
- Quality presets: `four_step` · `aggressive` · `fast` · `balanced` · `close`
- Ref2VA: ordered image / video / audio references (`Picture N`, `Video N`); cannot mix with first/last-frame

Do not mix first/last-frame anchors with Ref2VA references.

## Layout

```
server.py              WS + embedded UI
web_ui.py              HTTP API, library, jobs
h3_backend.py          one-shot h3.c process manager
h3_media.py            frame snap, last-frame extract, concat
web/                   React UI
third_party/h3.c       submodule
scripts/build_h3.sh
scripts/download_model.py
models/                gitignored MiniMax-H3 snapshot
web_outputs/
```
