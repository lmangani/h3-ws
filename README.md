# h3-ws

Local **MiniMax-H3** video+audio generation on Apple Silicon. Native [h3.c](https://github.com/antirez/h3.c) (Metal) with a browser UI.

Open **http://127.0.0.1:8765/** after starting the server.

## Requirements

- Apple Silicon Mac (Metal)
- Python 3.11+ with packages from `requirements.txt` (PyAV `av` for all media, including the h3.c mux/decode shim)
- Node.js 18+ (to build the UI)
- Xcode command-line tools (`make`)
- Enough unified memory for the model (~40 GB peak). Use `--ssd-streaming` if you have under ~64 GB.

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

Weights come from [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) (`FL2VA/` and `Ref2VA/`) and land in `models/MiniMax-H3`.

## Run

```bash
source .venv/bin/activate
python server.py
```

Or double-click `Start H3-WS.command`.

Default generate is 512×512, ~0.9 s (22 frames), **balanced** quality. Lower RAM:

```bash
python server.py --ssd-streaming
```

## Generate

Prompts should describe scene, action, camera, look, and audio. What you type is what H3 sees — there is no cloud rewriter.

| Mode | Use |
|------|-----|
| Text to video+audio | Prompt only |
| First / last / first+last frame | Image anchors |
| Ordered references (Ref2VA) | Image, silent video, video, video+audio, and/or audio |

First/last-frame anchors cannot be mixed with Ref2VA references. For references, prompt with `Picture 1`, `Video 1`, `Audio 1` in list order. Standalone audio must accompany an image or video (≤9 images, ≤3 videos, ≤3 audio).

**Quality:** Four-step · Aggressive · Fast · Balanced (default) · Close.

**Canvas** (dropdown only): 256×256 preview, 512×512 (safest), 512×512 with 384 or 320 internal, 768×768, 1024×768, 768×1024, 1344×768, 768×1344. H3-Base is a 768p model; 512×512 is the usual working size.

Output is 24 fps video with 32 kHz stereo audio. Duration snaps up to a legal H3 length (about 0.9 s, 1.6 s, 2.3 s, 4.5 s, 10 s, 15 s).
