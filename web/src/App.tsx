import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { clipDisplayPrompt, snapshotFromClip } from "./clipEditor";
import { applyProgressEvent } from "./progress";
import { captureVideoFrame, formatVideoTime } from "./frameCapture";
import { RefList, refsAreValid } from "./RefList";
import type { Clip, Config, LibraryFrame, PresetOption, ProgressState, ReferenceItem } from "./types";

function resolutionGroups(presets: PresetOption[]): { group: string; items: PresetOption[] }[] {
  const order: { group: string; items: PresetOption[] }[] = [];
  const index = new Map<string, number>();
  for (const preset of presets) {
    const group = preset.group || "";
    let slot = index.get(group);
    if (slot === undefined) {
      slot = order.length;
      index.set(group, slot);
      order.push({ group, items: [] });
    }
    order[slot].items.push(preset);
  }
  return order;
}

const API = "";
const BLOB_VIDEO_PREFIX = "blob:";
const NOTIFY_READY_KEY = "h3-ws-notify-on-ready";
const NOTIFY_ASKED_KEY = "h3-ws-notify-permission-asked";

function isHttpsContext(): boolean {
  try {
    return typeof location !== "undefined" && location.protocol === "https:";
  } catch {
    return false;
  }
}

function persistNotifyDecision(enabled: boolean) {
  try {
    localStorage.setItem(NOTIFY_ASKED_KEY, "1");
    localStorage.setItem(NOTIFY_READY_KEY, enabled ? "1" : "0");
  } catch {
    /* ignore */
  }
}

async function maybeRequestNotifyPermissionOnHttps(): Promise<void> {
  if (!isHttpsContext()) return;
  if (typeof Notification === "undefined") return;
  try {
    if (localStorage.getItem(NOTIFY_ASKED_KEY) === "1") return;
  } catch {
    return;
  }
  if (Notification.permission === "granted") {
    persistNotifyDecision(true);
    return;
  }
  if (Notification.permission === "denied") {
    persistNotifyDecision(false);
    return;
  }
  try {
    const permission = await Notification.requestPermission();
    persistNotifyDecision(permission === "granted");
  } catch {
    persistNotifyDecision(false);
  }
}

function notifyGenerationReady(body = "Your video is ready to play.") {
  if (!isHttpsContext()) return;
  try {
    if (localStorage.getItem(NOTIFY_READY_KEY) !== "1") return;
  } catch {
    return;
  }
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try {
    new Notification("H3-WS", { body, tag: "h3-ws-generation-ready" });
  } catch {
    /* ignore */
  }
}

function revokeClipBlob(clip: Clip) {
  if (clip.video_url?.startsWith(BLOB_VIDEO_PREFIX)) URL.revokeObjectURL(clip.video_url);
}

function revokeBlobVideoUrls(clips: Clip[]) {
  for (const clip of clips) revokeClipBlob(clip);
}

function preserveBlobVideoUrls(prev: Clip[], incoming: Clip[]): Clip[] {
  const blobById = new Map(
    prev
      .filter((c) => c.video_url?.startsWith(BLOB_VIDEO_PREFIX))
      .map((c) => [c.id, c.video_url] as const),
  );
  return incoming.map((c) => {
    const blob = blobById.get(c.id);
    return blob ? { ...c, video_url: blob } : c;
  });
}

function mergeClips(prev: Clip[], incoming: Clip[]): Clip[] {
  const byId = new Map(prev.map((c) => [c.id, c]));
  for (const c of incoming) byId.set(c.id, c);
  return Array.from(byId.values());
}

function replaceChainClips(prev: Clip[], chainId: string, chainClips: Clip[]): Clip[] {
  const rest = prev.filter((c) => c.chain_id !== chainId);
  return [...rest, ...preserveBlobVideoUrls(prev, chainClips)];
}

function formatBytes(n?: number) {
  if (!n) return "";
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const ROUNDED_DURATIONS: [number, number][] = [
  [22, 1],
  [56, 2],
  [107, 5],
  [243, 10],
  [362, 15],
];

function formatDuration(frames?: number, fps = 24) {
  if (!frames) return "";
  const hit = ROUNDED_DURATIONS.find(([nf]) => nf === frames);
  if (hit) return `${hit[1]}s`;
  return `${Math.round(frames / fps)}s`;
}

function pickPlaybackClip(clips: Clip[], chainId: string): string | null {
  const chain = clips.filter((c) => c.chain_id === chainId && c.status === "done" && c.video_url);
  const merged = chain.find((c) => c.label === "MERGED");
  const current = chain.find((c) => c.label === "CURRENT");
  const latest = [...chain].sort((a, b) => b.clip_index - a.clip_index)[0];
  return merged?.id ?? current?.id ?? latest?.id ?? null;
}

async function fetchConfig(): Promise<Config> {
  const r = await fetch(`${API}/api/config`);
  if (!r.ok) throw new Error("Failed to load config");
  return r.json();
}

async function fetchClips(chainId?: string): Promise<Clip[]> {
  const q = chainId ? `?chain_id=${encodeURIComponent(chainId)}` : "";
  const r = await fetch(`${API}/api/clips${q}`);
  if (!r.ok) throw new Error("Failed to load clips");
  const data = await r.json();
  return data.clips as Clip[];
}

async function fetchFrames(): Promise<LibraryFrame[]> {
  const r = await fetch(`${API}/api/frames`);
  if (!r.ok) throw new Error("Failed to load frames");
  const data = await r.json();
  return (data.frames ?? []) as LibraryFrame[];
}

async function uploadFile(file: File, kind: string): Promise<string> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API}/api/upload?kind=${encodeURIComponent(kind)}`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) throw new Error("Upload failed");
  const data = await r.json();
  return data.path as string;
}

const IMAGE_MODES = new Set(["first_frame", "fl2va"]);
const END_IMAGE_MODES = new Set(["last_frame", "fl2va"]);

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [frameLibrary, setFrameLibrary] = useState<LibraryFrame[]>([]);
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState("t2va");
  const [quality, setQuality] = useState("balanced");
  const [resolutionId, setResolutionId] = useState("512x512");
  const [durationId, setDurationId] = useState("1s");
  const [clipMultiplier, setClipMultiplier] = useState(1);
  const [numSteps, setNumSteps] = useState(20);
  const [seed, setSeed] = useState("");
  const [ssdStreaming, setSsdStreaming] = useState(false);
  const [tokenReduction, setTokenReduction] = useState(false);
  const [showOptions, setShowOptions] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressState | null>(null);
  const [chainId, setChainId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [imagePath, setImagePath] = useState<string | null>(null);
  const [imageName, setImageName] = useState<string | null>(null);
  const [endImagePath, setEndImagePath] = useState<string | null>(null);
  const [endImageName, setEndImageName] = useState<string | null>(null);
  const [refs, setRefs] = useState<ReferenceItem[]>([]);
  const [savingFrame, setSavingFrame] = useState(false);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const playerVideoRef = useRef<HTMLVideoElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const endImageRef = useRef<HTMLInputElement>(null);
  const runEventSourceRef = useRef<EventSource | null>(null);
  const clipsRef = useRef<Clip[]>([]);
  clipsRef.current = clips;

  const libraryClips = useMemo(
    () => clips.filter((c) => c.status === "done" && c.video_url),
    [clips],
  );
  const activeClip = useMemo(() => {
    if (selectedClipId) return clips.find((c) => c.id === selectedClipId) ?? null;
    return libraryClips[libraryClips.length - 1] ?? null;
  }, [clips, selectedClipId, libraryClips]);
  const chainParts = useMemo(
    () => (chainId ? clips.filter((c) => c.chain_id === chainId) : []),
    [clips, chainId],
  );
  const showChainPicker = chainParts.filter((c) => c.video_url).length > 1;
  const resolution = useMemo(() => {
    const p = config?.resolution_presets.find((r) => r.id === resolutionId);
    return {
      width: p?.width ?? 512,
      height: p?.height ?? 512,
      render_width: p?.render_width,
      render_height: p?.render_height,
      guidance: p?.guidance,
    };
  }, [config, resolutionId]);
  const durationPreset = config?.duration_presets.find((d) => d.id === durationId);
  const isRef2va = mode === "ref2va" || refs.length > 0;
  const isMultiClip = clipMultiplier > 1 && !isRef2va;
  const needsFirst = IMAGE_MODES.has(mode) && !isRef2va;
  const needsLast = END_IMAGE_MODES.has(mode) && !isRef2va;
  const lastUsesPrimaryUpload = mode === "last_frame" && !isRef2va;
  const previewCanvas = resolutionId === "256x256";
  const aggressiveInternal = resolutionId === "512x512-aggressive";
  const tokenReductionLocked = previewCanvas || aggressiveInternal || quality === "aggressive";

  useEffect(() => {
    void maybeRequestNotifyPermissionOnHttps();
    fetchConfig()
      .then((cfg) => {
        setConfig(cfg);
        setQuality(cfg.defaults.quality ?? "balanced");
        setNumSteps(cfg.defaults.num_steps);
        setSsdStreaming(Boolean(cfg.recommend_ssd_streaming));
        const defRes =
          cfg.resolution_presets.find((r) => r.id === "512x512") ??
          cfg.resolution_presets.find(
            (r) =>
              r.width === cfg.defaults.width &&
              r.height === cfg.defaults.height &&
              !r.render_width,
          );
        if (defRes) setResolutionId(defRes.id);
        const defDur = cfg.duration_presets.find((d) => d.num_frames === cfg.defaults.num_frames);
        if (defDur) setDurationId(defDur.id);
      })
      .catch((e) => setError(String(e)));
    fetchClips().then(setClips).catch(() => undefined);
    fetchFrames().then(setFrameLibrary).catch(() => undefined);
    return () => {
      runEventSourceRef.current?.close();
      revokeBlobVideoUrls(clipsRef.current);
    };
  }, []);

  const applyClipSelection = useCallback(
    (clip: Clip) => {
      setSelectedClipId(clip.id);
      setChainId(clip.chain_id);
      if (!config) return;
      const snap = snapshotFromClip(clip, config, {
        numSteps: config.defaults.num_steps,
        quality: config.defaults.quality ?? "balanced",
      });
      setPrompt(snap.prompt);
      setMode(snap.mode);
      setResolutionId(snap.resolutionId);
      setDurationId(snap.durationId);
      setClipMultiplier(snap.clipMultiplier);
      setNumSteps(snap.numSteps);
      setSeed(snap.seed);
      setQuality(snap.quality);
    },
    [config],
  );

  async function startNewProject() {
    setClips((prev) => {
      revokeBlobVideoUrls(prev);
      return [];
    });
    setChainId(null);
    setSelectedClipId(null);
    setPrompt("");
    setClipMultiplier(1);
    setBusy(false);
    setProgress(null);
    setError(null);
    setImagePath(null);
    setImageName(null);
    setEndImagePath(null);
    setEndImageName(null);
    setRefs([]);
    try {
      await fetch(`${API}/api/session/clear`, { method: "POST" });
    } catch (err) {
      console.warn("Session clear failed", err);
    }
  }

  async function deleteClip(clip: Clip) {
    await fetch(`${API}/api/clips/${clip.id}`, { method: "DELETE" });
    setClips((prev) => {
      revokeClipBlob(clip);
      return prev.filter((c) => c.id !== clip.id);
    });
    if (selectedClipId === clip.id) setSelectedClipId(null);
    setRefs((prev) =>
      prev.filter((r) => r.path !== clip.path && r.path !== clip.filename && !r.path.endsWith(`/${clip.filename}`)),
    );
  }

  async function saveCurrentFrame() {
    const video = playerVideoRef.current;
    if (!video || !activeClip) return;
    setSavingFrame(true);
    try {
      const blob = await captureVideoFrame(video);
      const fd = new FormData();
      fd.append("file", blob, "frame.png");
      fd.append("source_clip_id", activeClip.id);
      fd.append("time_s", String(video.currentTime));
      fd.append("label", `Frame @ ${formatVideoTime(video.currentTime)}`);
      const r = await fetch(`${API}/api/frames`, { method: "POST", body: fd });
      if (!r.ok) throw new Error("Could not save frame");
      setFrameLibrary(await fetchFrames());
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingFrame(false);
    }
  }

  function handleRefsChange(next: ReferenceItem[]) {
    setRefs(next);
    if (next.length === 0) return;
    setMode("ref2va");
    setImagePath(null);
    setImageName(null);
    setEndImagePath(null);
    setEndImageName(null);
    setClipMultiplier(1);
  }

  function applyFrameAsInput(frame: LibraryFrame, which: "start" | "end") {
    if (mode === "ref2va") {
      if (refs.some((r) => r.path === frame.path)) return;
      handleRefsChange([
        ...refs,
        { id: crypto.randomUUID(), kind: "image", path: frame.path, name: frame.label },
      ]);
      return;
    }
    setRefs([]);
    if (which === "end") {
      setEndImagePath(frame.path);
      setEndImageName(frame.label);
      if (mode === "t2va" || mode === "first_frame") setMode("fl2va");
      return;
    }
    setImagePath(frame.path);
    setImageName(frame.label);
    if (mode === "t2va") setMode("first_frame");
  }

  async function deleteFrame(frame: LibraryFrame) {
    const r = await fetch(`${API}/api/frames/${frame.id}`, { method: "DELETE" });
    if (!r.ok) return;
    const data = await r.json();
    setFrameLibrary((data.frames ?? []) as LibraryFrame[]);
    if (imagePath === frame.path) {
      setImagePath(null);
      setImageName(null);
    }
    if (endImagePath === frame.path) {
      setEndImagePath(null);
      setEndImageName(null);
    }
    setRefs((prev) => prev.filter((r) => r.path !== frame.path));
  }

  async function cancelActiveRun() {
    if (!activeRunId) return;
    await fetch(`${API}/api/runs/${activeRunId}/cancel`, { method: "POST" });
  }

  function subscribeRun(runId: string, runChainId: string) {
    runEventSourceRef.current?.close();
    setActiveRunId(runId);
    const es = new EventSource(`${API}/api/runs/${runId}/events`);
    runEventSourceRef.current = es;
    let closed = false;
    const finishRun = () => {
      if (closed) return;
      closed = true;
      es.close();
      runEventSourceRef.current = null;
      setActiveRunId(null);
      setBusy(false);
      setProgress(null);
      notifyGenerationReady();
    };
    es.onmessage = (ev) => {
      const msg = JSON.parse(ev.data) as Record<string, unknown>;
      if (msg.type === "ping") return;
      if (msg.type === "progress" || msg.type === "generation_keepalive") {
        setProgress((prev) => applyProgressEvent(prev, msg));
        return;
      }
      if (msg.type === "clip_started") {
        setProgress({
          phase: "generating",
          message: `Clip ${Number(msg.index ?? 0) + 1}/${Number(msg.total ?? 1)}`,
        });
      }
      if (msg.type === "clip_done" || msg.type === "merged") {
        const clipId = String(msg.clip_id ?? "");
        const videoUrl = String(msg.video_url ?? "");
        fetchClips(runChainId).then((chainClips) => {
          setClips((prev) => replaceChainClips(prev, runChainId, chainClips));
          setSelectedClipId(pickPlaybackClip(chainClips, runChainId) ?? clipId ?? null);
        });
        void videoUrl;
      }
      if (msg.type === "run_cancelled") {
        setProgress({ phase: "cancelled", message: String(msg.message || "Cancelled") });
        finishRun();
      } else if (msg.type === "run_complete" || msg.type === "run_done") {
        finishRun();
      } else if (msg.type === "error" || msg.type === "clip_failed") {
        setError(String(msg.error || msg.message || "Failed"));
        finishRun();
      }
    };
    es.onerror = () => {
      if (closed) return;
      closed = true;
      es.close();
      runEventSourceRef.current = null;
      setBusy(false);
      setProgress(null);
      setError((prev) => prev ?? "Lost connection to server while waiting for progress.");
    };
  }

  async function handleGenerate() {
    if (!canSubmit || !prompt.trim() || busy) return;
    setError(null);
    setBusy(true);
    setProgress({ phase: "starting", message: "Submitting…" });
    const body: Record<string, unknown> = {
      prompt: prompt.trim(),
      mode: isRef2va ? "ref2va" : mode,
      quality,
      width: resolution.width,
      height: resolution.height,
      duration_seconds: durationPreset?.seconds,
      num_frames: durationPreset?.num_frames,
      clip_count: isRef2va ? 1 : clipMultiplier,
      num_steps: numSteps,
      autocontinue: isMultiClip,
      autoconcat: isMultiClip,
      ssd_streaming: ssdStreaming,
      token_reduction: tokenReductionLocked ? false : tokenReduction,
    };
    if (resolution.render_width) body.render_width = resolution.render_width;
    if (resolution.render_height) body.render_height = resolution.render_height;
    if (seed.trim() !== "") body.seed = Number(seed);
    if (isRef2va) {
      body.refs = refs.map((r) => ({
        kind: r.kind,
        path: r.path,
        name: r.name,
        audio_path: r.audioPath || undefined,
      }));
    } else {
      if (needsFirst && imagePath) body.image_path = imagePath;
      if (lastUsesPrimaryUpload && imagePath) body.end_image_path = imagePath;
      if (needsLast && !lastUsesPrimaryUpload && endImagePath) body.end_image_path = endImagePath;
    }
    try {
      const r = await fetch(`${API}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        const detail = (err as { detail?: unknown }).detail;
        throw new Error(typeof detail === "string" ? detail : "Generate failed");
      }
      const data = await r.json();
      setChainId(data.chain_id);
      setSelectedClipId(null);
      setProgress(
        data.started_immediately
          ? { phase: "starting", message: "Starting…" }
          : { phase: "queued", message: "Queued — waiting for current job…" },
      );
      subscribeRun(data.run_id, data.chain_id);
      const chainClips = await fetchClips(data.chain_id);
      setClips((prev) => mergeClips(prev, chainClips));
    } catch (e) {
      setError(String(e));
      setBusy(false);
      setProgress(null);
    }
  }

  const serverOk = config?.server_connected;
  const canSubmit = useMemo(() => {
    if (!prompt.trim() || busy || !serverOk) return false;
    if (isRef2va) return refsAreValid(refs).ok;
    if (needsFirst && !imagePath) return false;
    if (mode === "last_frame" && !imagePath && !endImagePath) return false;
    if (mode === "fl2va" && (!imagePath || !endImagePath)) return false;
    return true;
  }, [prompt, busy, serverOk, isRef2va, refs, needsFirst, imagePath, endImagePath, mode]);

  const fitPromptHeight = useCallback(() => {
    const el = promptRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  useLayoutEffect(() => {
    fitPromptHeight();
  }, [prompt, fitPromptHeight]);

  const endpointLabel = useMemo(() => {
    if (typeof window === "undefined") return config?.server_url ?? "";
    return `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
  }, [config?.server_url]);

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="brand-mark">H3-WS</span>
          <span className="brand-sub">MiniMax-H3</span>
        </div>
        <div className="header-status">
          <button type="button" className="btn-secondary" onClick={() => void startNewProject()}>
            New project
          </button>
          <span className={`status-dot ${serverOk ? "ok" : "off"}`} title={endpointLabel} />
          {serverOk ? "Server connected" : "Server offline"}
        </div>
      </header>

      <div className="app-body">
        <div className="app-main">
          <section className="player-section">
            <div className="player-wrap">
              {activeClip?.video_url ? (
                <video
                  ref={playerVideoRef}
                  className="player"
                  src={activeClip.video_url}
                  controls
                  loop
                  playsInline
                  preload="metadata"
                />
              ) : (
                <div className="player placeholder">
                  {busy ? progress?.message ?? "Generating…" : "Your video will appear here"}
                </div>
              )}
              {busy && (
                <div className="progress-overlay">
                  <div className="progress-bar">
                    {progress?.pct != null ? (
                      <div className="progress-fill" style={{ width: `${Math.min(100, progress.pct)}%` }} />
                    ) : (
                      <div className="progress-pulse" />
                    )}
                  </div>
                  <div className="progress-overlay-row">
                    <span>{progress?.message ?? "Working…"}</span>
                    {activeRunId && progress?.phase !== "cancelled" && (
                      <button type="button" className="btn-cancel" onClick={() => void cancelActiveRun()}>
                        Cancel
                      </button>
                    )}
                  </div>
                </div>
              )}
              {activeClip?.video_url && !busy && (
                <button
                  type="button"
                  className="player-capture-btn"
                  disabled={savingFrame}
                  onClick={() => void saveCurrentFrame()}
                  title="Save frame to library"
                >
                  {savingFrame ? (
                    <span className="player-capture-spinner" aria-hidden />
                  ) : (
                    <svg className="player-capture-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
                      <path d="M4 7h2l2-3h8l2 3h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z" />
                      <circle cx="12" cy="13" r="4" />
                    </svg>
                  )}
                </button>
              )}
            </div>
            {error && <div className="error-banner">{error}</div>}
            {showChainPicker && (
              <div className="player-context">
                <div className="player-context-body">
                  <label className="player-context-label">
                    Chain clip
                    <select
                      className="chain-picker-select"
                      value={selectedClipId ?? ""}
                      onChange={(e) => {
                        const c = chainParts.find((x) => x.id === e.target.value);
                        if (c) applyClipSelection(c);
                      }}
                    >
                      {chainParts.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.label}
                          {c.num_frames ? ` · ${formatDuration(c.num_frames, config?.defaults.fps ?? 24)}` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
            )}
          </section>

          <section className="composer">
            <div className="prompt-row">
              <div className="prompt-field-wrap">
                <textarea
                  ref={promptRef}
                  className="prompt-input"
                  rows={1}
                  placeholder="Scene, action, camera, look, and audio…"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void handleGenerate();
                    }
                  }}
                  disabled={busy}
                />
                <div className="prompt-field-actions">
                  <button
                    type="button"
                    className="btn-prompt-action"
                    onClick={() => setPrompt("")}
                    disabled={busy || !prompt}
                  >
                    Clear
                  </button>
                </div>
              </div>
              <button type="button" className="btn-generate" onClick={() => void handleGenerate()} disabled={!canSubmit}>
                ↑
              </button>
            </div>

            <button type="button" className="options-toggle" onClick={() => setShowOptions((v) => !v)}>
              {showOptions ? "Hide options" : "Show options"}
            </button>

            {showOptions && config && (
              <div className="options-panel">
                <p className="hint">{config.model_note}</p>
                {config.engine_ok === false && config.engine_error && (
                  <p className="hint hint-inline">{config.engine_error}</p>
                )}

                <div className="options-grid options-grid-compact">
                  <label className="opt-mode">
                    Mode
                    <select
                      value={mode}
                      onChange={(e) => {
                        const next = e.target.value;
                        setMode(next);
                        setImagePath(null);
                        setImageName(null);
                        setEndImagePath(null);
                        setEndImageName(null);
                        if (next !== "ref2va") setRefs([]);
                      }}
                    >
                      {(config.generation_modes ?? []).map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Quality
                    <select
                      value={quality}
                      onChange={(e) => {
                        setQuality(e.target.value);
                        if (e.target.value === "four_step") setNumSteps(4);
                        if (e.target.value === "close") setNumSteps(50);
                        if (e.target.value === "balanced" || e.target.value === "fast" || e.target.value === "aggressive") {
                          setNumSteps(20);
                        }
                        setTokenReduction(e.target.value === "fast");
                      }}
                    >
                      {config.quality_presets.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="opt-resolution">
                    Resolution
                    <select
                      value={resolutionId}
                      onChange={(e) => {
                        const next = e.target.value;
                        setResolutionId(next);
                        if (next === "256x256" || next === "512x512-aggressive") {
                          setTokenReduction(false);
                        }
                      }}
                    >
                      {resolutionGroups(config.resolution_presets).map((g) =>
                        g.group ? (
                          <optgroup key={g.group} label={g.group}>
                            {g.items.map((r) => (
                              <option key={r.id} value={r.id}>
                                {r.label}
                              </option>
                            ))}
                          </optgroup>
                        ) : (
                          g.items.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.label}
                            </option>
                          ))
                        ),
                      )}
                    </select>
                  </label>
                  <label className="opt-narrow">
                    Duration
                    <select value={durationId} onChange={(e) => setDurationId(e.target.value)}>
                      {config.duration_presets.map((d) => (
                        <option key={d.id} value={d.id} title={d.label}>
                          {d.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="opt-narrow">
                    Clips
                    <select
                      value={clipMultiplier}
                      onChange={(e) => setClipMultiplier(Number(e.target.value))}
                      disabled={isRef2va}
                    >
                      {Array.from({ length: config.clip_multiplier_max ?? 10 }, (_, i) => i + 1).map((n) => (
                        <option key={n} value={n}>
                          ×{n}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="opt-narrow">
                    Steps
                    <input
                      type="number"
                      min={1}
                      max={50}
                      value={numSteps}
                      onChange={(e) => setNumSteps(Number(e.target.value))}
                    />
                  </label>
                  <label className="opt-seed">
                    Seed
                    <input
                      type="text"
                      placeholder="random"
                      value={seed}
                      onChange={(e) => setSeed(e.target.value)}
                    />
                  </label>
                </div>

                <div className="options-checks">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={ssdStreaming}
                      onChange={(e) => setSsdStreaming(e.target.checked)}
                    />
                    SSD streaming (lower RAM)
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={tokenReduction && !tokenReductionLocked}
                      onChange={(e) => setTokenReduction(e.target.checked)}
                      disabled={tokenReductionLocked}
                    />
                    Token reduction
                  </label>
                </div>

                {resolution.guidance && <p className="hint hint-inline">{resolution.guidance}</p>}

                {(needsFirst || needsLast || lastUsesPrimaryUpload) && (
                  <div className="options-uploads">
                    <span className="media-panel-title">
                      {mode === "fl2va"
                        ? "First and last frames"
                        : mode === "last_frame"
                          ? "Last frame"
                          : "First frame"}
                    </span>
                    {(needsFirst || lastUsesPrimaryUpload) && (
                      <label className="media-upload">
                        <span className="media-upload-label">
                          {mode === "last_frame" ? "Last frame" : "First frame"}
                        </span>
                        <input
                          ref={imageRef}
                          type="file"
                          accept="image/*"
                          onChange={async (e) => {
                            const f = e.target.files?.[0];
                            if (f) {
                              setImagePath(await uploadFile(f, "image"));
                              setImageName(f.name);
                            }
                          }}
                        />
                        <span className="media-upload-hint">{imageName ?? "Choose image…"}</span>
                      </label>
                    )}
                    {mode === "fl2va" && (
                      <label className="media-upload">
                        <span className="media-upload-label">Last frame</span>
                        <input
                          ref={endImageRef}
                          type="file"
                          accept="image/*"
                          onChange={async (e) => {
                            const f = e.target.files?.[0];
                            if (f) {
                              setEndImagePath(await uploadFile(f, "image"));
                              setEndImageName(f.name);
                            }
                          }}
                        />
                        <span className="media-upload-hint">{endImageName ?? "Choose image…"}</span>
                      </label>
                    )}
                  </div>
                )}

                {mode === "ref2va" && (
                  <RefList
                    refs={refs}
                    disabled={busy}
                    frames={frameLibrary}
                    clips={libraryClips}
                    onChange={handleRefsChange}
                    uploadFile={uploadFile}
                  />
                )}

                {isMultiClip && (
                  <p className="hint">
                    ×{clipMultiplier} clips will chain last frame → first frame and merge the result.
                  </p>
                )}

                {activeClip && (
                  <p className="meta">
                    Viewing: {formatDuration(activeClip.num_frames, config.defaults.fps)}
                    {activeClip.width && activeClip.height ? ` · ${activeClip.width}×${activeClip.height}` : ""}
                    {activeClip.bytes ? ` · ${formatBytes(activeClip.bytes)}` : ""}
                  </p>
                )}
              </div>
            )}
          </section>
        </div>

        <aside className="library">
          <div className="library-header">
            <span className="library-title">Library</span>
            <span className="library-count">{libraryClips.length}</span>
          </div>
          <div className="library-grid">
            {libraryClips.map((clip) => (
              <div
                key={clip.id}
                className={`library-card-wrap ${activeClip?.id === clip.id ? "active" : ""}`}
              >
                <button
                  type="button"
                  className="library-card"
                  onClick={() => applyClipSelection(clip)}
                  title={clip.prompt}
                >
                  {clip.video_url && (
                    <video className="library-thumb" src={clip.video_url} muted playsInline preload="metadata" />
                  )}
                  <span className={`library-label ${clip.label.toLowerCase()}`}>{clip.label}</span>
                  <span className="library-prompt">{clipDisplayPrompt(clip.prompt)}</span>
                </button>
                <button
                  type="button"
                  className="library-delete"
                  title="Delete"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void deleteClip(clip);
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>

          <div className="library-section">
            <div className="library-header">
              <span className="library-title">Frames</span>
              <span className="library-count">{frameLibrary.length}</span>
            </div>
            {frameLibrary.length === 0 ? (
              <p className="library-empty-hint">
                Pause a video and tap the camera icon to capture stills. Use them as a
                first or last frame, or as a reference image when that mode is selected.
              </p>
            ) : (
              <div className="frame-library-grid">
                {frameLibrary.map((frame) => (
                  <div
                    key={frame.id}
                    className={`frame-card-wrap ${
                      imagePath === frame.path || endImagePath === frame.path ? "active" : ""
                    }`}
                  >
                    <button
                      type="button"
                      className="frame-card"
                      title={`Use as start image: ${frame.label}`}
                      onClick={() => applyFrameAsInput(frame, "start")}
                    >
                      <img className="frame-thumb" src={frame.image_url} alt={frame.label} loading="lazy" />
                      <span className="frame-label">{frame.label}</span>
                    </button>
                    {mode === "fl2va" && (
                      <button
                        type="button"
                        className="frame-use-end"
                        title="Use as end image"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          applyFrameAsInput(frame, "end");
                        }}
                      >
                        End
                      </button>
                    )}
                    <button
                      type="button"
                      className="library-delete"
                      title="Delete frame"
                      disabled={busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        void deleteFrame(frame);
                      }}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
