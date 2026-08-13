export interface QualityPreset {
  id: string;
  label: string;
  steps?: number;
  layers?: number;
  reuse?: number;
  token_reduction?: boolean;
  guidance?: string | null;
}

export interface PresetOption {
  id: string;
  label: string;
  width?: number;
  height?: number;
  render_width?: number;
  render_height?: number;
  seconds?: number;
  num_frames?: number;
  guidance?: string;
  aspect?: string;
  group?: string;
}

export interface Config {
  server_connected: boolean;
  server_url: string;
  engine_ok?: boolean;
  engine_error?: string | null;
  h3_bin?: string;
  model_dir?: string;
  ram_gb?: number | null;
  recommend_ssd_streaming?: boolean;
  metal4?: boolean;
  quality_presets: QualityPreset[];
  resolution_presets: PresetOption[];
  duration_presets: PresetOption[];
  generation_modes: { id: string; label: string }[];
  ref_kinds?: { id: string; label: string; flag: string }[];
  defaults: {
    num_frames: number;
    width: number;
    height: number;
    num_steps: number;
    layers?: number;
    reuse?: number;
    fps: number;
    quality?: string;
  };
  model_note: string;
  clip_multiplier_max?: number;
  embedded?: boolean;
  web_url?: string;
  pyav_available?: boolean;
}

export interface Clip {
  id: string;
  prompt: string;
  label: string;
  video_url: string;
  filename: string;
  path?: string;
  chain_id: string;
  clip_index: number;
  mode: string;
  status: string;
  created_at: string;
  elapsed_s?: number;
  bytes?: number;
  error?: string;
  num_frames?: number;
  width?: number;
  height?: number;
  seed?: number;
  num_steps?: number;
  layers?: number;
  reuse?: number;
  duration_seconds?: number;
  clip_count?: number;
  autocontinue?: boolean;
  autoconcat?: boolean;
  quality?: string;
}

export interface LibraryFrame {
  id: string;
  label: string;
  path: string;
  image_url: string;
  filename: string;
  width?: number;
  height?: number;
  source_clip_id?: string;
  time_s?: number;
  created_at: string;
}

export interface ModelProgress {
  stage?: string;
  step?: number;
  total?: number;
  pct?: number;
  eta_s?: number;
  avg_step_s?: number;
  elapsed_s?: number;
  label?: string;
}

export type RefKind = "image" | "silent_video" | "video" | "video_audio" | "audio";

export interface ReferenceItem {
  id: string;
  kind: RefKind;
  path: string;
  name: string;
  audioPath?: string;
  audioName?: string;
}

export interface ProgressState {
  phase: string;
  message: string;
  elapsed_s?: number;
  kb?: number;
  stage?: string;
  step?: number;
  total?: number;
  pct?: number;
  eta_s?: number;
}
