# AGENTS.md

Canonical guide for AI agents using **h3-ws** to generate video on Apple Silicon.

**Read [`ROADMAP.md`](ROADMAP.md)** for architecture and phase status. Prompting for H3 should follow a Context-IR skeleton (scene, action, camera, look, audio) — a full DIRECTOR.md lands in P7.

## Stack

| Piece | Role |
|-------|------|
| `server.py` | Local MiniMax-H3 inference via native **h3.c**. One job at a time. Embeds Web UI. |
| `h3_backend.py` | Spawns `./h3 -p … -o`. Resident interactive session is P5. |
| `web_ui.py` + `web/` | Browser library, quality presets, SSE progress. |
| Weights | `models/MiniMax-H3/{FL2VA,Ref2VA}` from `MiniMaxAI/MiniMax-H3` |

**Default Web UI:** http://127.0.0.1:8765/  
**WebSocket:** ws://127.0.0.1:8765/ws  

This stack does **not** run cloud prompt expansion. What you send is what H3 sees.

## Weights (mandatory)

Only the official MiniMax-H3 checkpoint trees. Not LTX, not PyTorch-only CUDA recipes.

```
hf download MiniMaxAI/MiniMax-H3 --include "model_index.json" "FL2VA/*" "Ref2VA/*" --local-dir models/MiniMax-H3
```

Or: `python scripts/download_model.py`

Build the engine: `./scripts/build_h3.sh` (needs the `third_party/h3.c` submodule).

## Frame math

H3 snaps **up** to `5 + 17n` at 24 fps (22, 39, 56, 107, 243, 362, …). Width and height must each be multiples of 32, at least 32, and their product must not exceed 768×1344. The UI only offers documented canvases (256×256 preview through 768×1344). H3-Base is a 768p model; 512×512 is the default development size.

## Modes (v1)

- `t2va` — text to video+audio (FL2VA)
- `first_frame` / `last_frame` / `fl2va` — image anchors
- `ref2va` — ordered image / silent video / video / video+audio / audio references (Ref2VA)

Do not mix first/last-frame anchors with Ref2VA references. Prompt Ref2VA with `Picture N` / `Video N` / `Audio N` in list order. Standalone audio must accompany an image or video. Limits: ≤9 images, ≤3 videos, ≤3 audio, mixed files ≤12.

## Quality presets

`four_step` · `aggressive` · `fast` · `balanced` (default) · `close`

`--reuse` and `--core-reuse` are mutually exclusive. Do not combine token-reduction with `--layers 40 --reuse 3`.
