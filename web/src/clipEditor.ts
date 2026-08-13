import type { Clip, Config } from "./types";

export function clipDisplayPrompt(prompt: string): string {
  return prompt.replace(/\s*\(×\d+ merged\)\s*$/i, "").trim();
}

export function resolutionIdForClip(clip: Clip, config: Config | null): string {
  if (!clip.width || !clip.height) return "512x512";
  const presets = config?.resolution_presets ?? [];
  const native = presets.find(
    (r) => r.width === clip.width && r.height === clip.height && !r.render_width,
  );
  if (native) return native.id;
  const match = presets.find((r) => r.width === clip.width && r.height === clip.height);
  return match?.id ?? `${clip.width}x${clip.height}`;
}

export function durationIdForClip(clip: Clip, config: Config | null): string {
  if (clip.num_frames && config?.duration_presets) {
    const match = config.duration_presets.find((d) => d.num_frames === clip.num_frames);
    if (match) return match.id;
  }
  if (clip.duration_seconds != null) {
    const match = config?.duration_presets.find((d) => d.seconds === clip.duration_seconds);
    if (match) return match.id;
  }
  return config?.duration_presets[0]?.id ?? "1s";
}

export interface ClipEditorSnapshot {
  prompt: string;
  mode: string;
  resolutionId: string;
  durationId: string;
  clipMultiplier: number;
  numSteps: number;
  layers: number;
  reuse: number;
  seed: string;
  quality: string;
}

export function snapshotFromClip(
  clip: Clip,
  config: Config | null,
  defaults: { numSteps: number; layers: number; reuse: number; quality: string },
): ClipEditorSnapshot {
  return {
    prompt: clipDisplayPrompt(clip.prompt),
    mode: clip.mode || "t2va",
    resolutionId: resolutionIdForClip(clip, config),
    durationId: durationIdForClip(clip, config),
    clipMultiplier: clip.clip_count ?? 1,
    numSteps: clip.num_steps ?? defaults.numSteps,
    layers: clip.layers ?? defaults.layers,
    reuse: clip.reuse ?? defaults.reuse,
    seed: clip.seed != null ? String(clip.seed) : "",
    quality: clip.quality ?? defaults.quality,
  };
}
