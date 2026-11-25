"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
    createProject,
    uploadProjectVideo,
    getProject,
    getSettings,
    saveSettings,
    getBrandKits,
    getCodexStatus,
    getApiBaseUrl,
    retryProject,
} from "@/lib/api";
import { BrandKit, Clip, CodexStatus, Settings } from "@/lib/types";
import { useProjectProgress } from "@/lib/websocket";
import { SystemSetup } from "@/components/SystemSetup";
import { Upload, Link2, Sliders, Sparkles } from "lucide-react";

const STAGE_LABELS: Record<string, string> = {
    downloading: "Preparing source video…",
    transcribing: "Extracting word-level transcript…",
    analyzing: "AI scoring viral moments…",
    processing: "Reframing & rendering clips…",
    done: "All clips ready!",
    error: "Pipeline failed",
};

const DURATION_PRESETS = [
    { label: "Short", range: "15–30s", min: 15, max: 30 },
    { label: "Standard", range: "30–60s", min: 30, max: 60 },
    { label: "Deep Dive", range: "60–90s", min: 60, max: 90 },
] as const;

const LAYOUT_PRESETS = [
    { value: "auto", label: "Auto Detect" },
    { value: "dynamic_zoom", label: "Smart Face Track" },
    { value: "podcast_split", label: "Podcast Split" },
    { value: "podcast_switch", label: "Podcast Speaker Switch" },
    { value: "tutorial", label: "Tutorial (Screen + Face)" },
    { value: "original", label: "Original + Blur Fill" },
] as const;

const FOCUS_PRESETS = [
    {
        label: "😂 Funny & Chaotic",
        prompt: "Find the funniest moments, unexpected jokes, hilarious banter, chaotic reactions, and laugh-out-loud stories.",
    },
    {
        label: "🎮 Stream Highlights",
        prompt: "Find the most entertaining livestream highlights, big reactions, chat-worthy moments, and memorable stories.",
    },
    {
        label: "🔥 Hot Takes",
        prompt: "Find the boldest opinions, controversial hot takes, heated debates, and mic-drop moments.",
    },
    {
        label: "💡 Actionable Tips",
        prompt: "Find the clearest actionable advice, surprising insights, and high-value takeaways.",
    },
] as const;

function formatDuration(s: number) {
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
}

export default function CreateProjectPage() {
    const router = useRouter();
    const [sourceMode, setSourceMode] = useState<"youtube" | "upload">("youtube");
    const [url, setUrl] = useState("");
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [uploadTitle, setUploadTitle] = useState("");

    const [minDur, setMinDur] = useState(30);
    const [maxDur, setMaxDur] = useState(60);
    const [layoutPref, setLayoutPref] = useState("auto");
    const [customPrompt, setCustomPrompt] = useState("");
    const [brandKits, setBrandKits] = useState<BrandKit[]>([]);
    const [selectedBrandKit, setSelectedBrandKit] = useState<string>("");

    const [isProcessing, setIsProcessing] = useState(false);
    const [generatedClips, setGeneratedClips] = useState<Clip[]>([]);
    const [projectId, setProjectId] = useState<string | null>(null);
    const [apiError, setApiError] = useState<string | null>(null);
    const [hasApiKey, setHasApiKey] = useState<boolean | null>(null);
    const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null);
    const [provider, setProvider] = useState<Settings["llm_provider"]>("codex");
    const [model, setModel] = useState("");
    const [failure, setFailure] = useState<string | null>(null);
    const [runKey, setRunKey] = useState(0);
    const [retrying, setRetrying] = useState(false);

    const logsEndRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const { stage, percent, message, logs } = useProjectProgress(projectId, runKey);

    useEffect(() => {
        Promise.all([getSettings(), getBrandKits(), getCodexStatus()])
            .then(([s, kits, cx]) => {
                setHasApiKey(Boolean(s.has_api_key));
                setProvider(s.llm_provider ?? "codex");
                setModel(s.llm_model ?? "");
                setBrandKits(kits);
                setCodexStatus(cx);
            })
            .catch(console.error);
    }, []);

    useEffect(() => {
        logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [logs]);

    useEffect(() => {
        if (stage === "done" && projectId) {
            getProject(projectId)
                .then((data) => {
                    setGeneratedClips(data.clips || []);
                    setIsProcessing(false);
                })
                .catch(() => {
                    setApiError("Failed to load clips.");
                    setIsProcessing(false);
                });
        } else if (stage === "error") {
            setIsProcessing(false);
            setFailure(message || "Pipeline failed");
        }
    }, [stage, projectId, message]);

    const handleRetry = async () => {
        if (!projectId || retrying) return;
        setRetrying(true);
        try {
            await retryProject(projectId);
            setFailure(null);
            setIsProcessing(true);
            setRunKey((k) => k + 1);
        } catch {
            setFailure("Could not restart the project. Is the backend running?");
        } finally {
            setRetrying(false);
        }
    };

    const handleStartOver = () => {
        setFailure(null);
        setProjectId(null);
    };

    const handlePaste = async () => {
        try {
            setUrl(await navigator.clipboard.readText());
        } catch {
            /* ignore */
        }
    };

    const handleGenerate = async (e: React.FormEvent) => {
        e.preventDefault();
        if (isProcessing) return;
        if (sourceMode === "youtube" && !url.trim()) return;
        if (sourceMode === "upload" && !selectedFile) {
            setApiError("Please select an MP4, MOV, MKV, or WEBM video file.");
            return;
        }

        setIsProcessing(true);
        setGeneratedClips([]);
        setApiError(null);
        setFailure(null);
        setProjectId(null);

        try {
            await saveSettings({ llm_provider: provider, llm_model: model });
            const opts = {
                clip_min_duration: minDur,
                clip_max_duration: maxDur,
                custom_prompt: customPrompt.trim() || undefined,
                layout_preference: layoutPref,
                brand_kit_id: selectedBrandKit || null,
            };

            const res =
                sourceMode === "upload" && selectedFile
                    ? await uploadProjectVideo(selectedFile, {
                          ...opts,
                          title: uploadTitle.trim() || undefined,
                      })
                    : await createProject(url.trim(), opts);

            setProjectId(res.project_id);
        } catch (err) {
            setApiError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
            setIsProcessing(false);
        }
    };

    const clampedPercent = Math.min(100, Math.max(0, percent));
    const isLive = isProcessing && projectId;

    return (
        <div className="mx-auto w-full max-w-3xl px-6 py-12">
            {!isLive && <SystemSetup className="mb-8" />}

            {hasApiKey === false && provider !== "ollama" && provider !== "codex" && (
                <div className="mb-8 flex items-center justify-between gap-4 rounded-2xl border border-amber-500/20 bg-amber-500/10 p-5">
                    <div className="flex items-center gap-3 text-amber-400">
                        <svg className="h-5 w-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                        </svg>
                        <div>
                            <p className="font-bold text-sm">No LLM API key configured</p>
                            <p className="text-xs opacity-80">Add an API key in Settings or switch provider to local Ollama.</p>
                        </div>
                    </div>
                    <Link href="/settings" className="flex-shrink-0 rounded-xl bg-amber-500/20 px-4 py-2 text-xs font-bold text-amber-400 hover:bg-amber-500/30 transition-colors">
                        Configure →
                    </Link>
                </div>
            )}

            {!isLive && !failure && generatedClips.length === 0 && (
                <div className="mb-8 text-center">
                    <span className="mb-4 inline-block rounded-full bg-primary/20 border border-primary/30 px-4 py-1 text-xs font-bold uppercase tracking-widest text-primary">
                        OpenClip Studio
                    </span>
                    <h1 className="text-4xl font-bold text-white mb-3">
                        Repurpose any video into <span className="text-gradient">viral shorts</span>
                    </h1>
                    <p className="text-slate-400 text-base max-w-lg mx-auto">
                        Paste a YouTube link or upload a local video. Auto-clip, smart-reframe, then fine-tune in the multi-track Studio Editor.
                    </p>
                </div>
            )}

            {/* Failed run */}
            {!isLive && failure && projectId && (
                <div className="mb-8 glass-card rounded-3xl p-6 border border-red-500/20">
                    <div className="flex items-start gap-4">
                        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-red-500/10 text-red-400">
                            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                            </svg>
                        </div>
                        <div className="min-w-0 flex-1">
                            <p className="font-bold text-white">That run didn&apos;t finish</p>
                            <p className="mt-1 text-sm text-slate-400 break-words">{failure}</p>
                            <p className="mt-2 text-xs text-slate-500">Retry continues from the last finished checkpoint.</p>
                            <div className="mt-4 flex flex-wrap gap-3">
                                <button type="button" onClick={handleRetry} disabled={retrying} className="rounded-xl bg-primary px-5 py-2.5 text-sm font-bold text-white glow-primary hover:bg-primary/90 transition-all disabled:opacity-60">
                                    {retrying ? "Restarting…" : "Retry"}
                                </button>
                                <button type="button" onClick={handleStartOver} className="rounded-xl glass glass-hover px-5 py-2.5 text-sm font-semibold text-slate-300 transition-all">
                                    Start over
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Main Creation Form */}
            {!isLive && !failure && generatedClips.length === 0 && (
                <form onSubmit={handleGenerate} className="glass-card rounded-3xl p-8 space-y-6">
                    {/* Source Mode Toggle */}
                    <div className="grid grid-cols-2 gap-2 p-1 rounded-2xl bg-slate-900/70 border border-white/10">
                        <button
                            type="button"
                            onClick={() => setSourceMode("youtube")}
                            className={`py-2.5 rounded-xl text-xs font-bold flex items-center justify-center gap-2 transition-all ${
                                sourceMode === "youtube"
                                    ? "bg-primary text-white shadow-lg"
                                    : "text-slate-400 hover:text-white"
                            }`}
                        >
                            <Link2 className="h-4 w-4" />
                            YouTube Link
                        </button>
                        <button
                            type="button"
                            onClick={() => setSourceMode("upload")}
                            className={`py-2.5 rounded-xl text-xs font-bold flex items-center justify-center gap-2 transition-all ${
                                sourceMode === "upload"
                                    ? "bg-primary text-white shadow-lg"
                                    : "text-slate-400 hover:text-white"
                            }`}
                        >
                            <Upload className="h-4 w-4" />
                            Upload Local Video
                        </button>
                    </div>

                    {/* Input Area */}
                    {sourceMode === "youtube" ? (
                        <div>
                            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                                YouTube URL
                            </label>
                            <div className="flex items-center gap-2 rounded-xl glass px-4 py-3 border border-white/10 focus-within:border-primary/50 transition-all">
                                <Link2 className="h-4 w-4 text-slate-400 flex-shrink-0" />
                                <input
                                    value={url}
                                    onChange={(e) => setUrl(e.target.value)}
                                    className="flex-1 bg-transparent text-sm text-white placeholder-slate-500 focus:outline-none"
                                    placeholder="https://youtube.com/watch?v=..., /live/..., or https://youtu.be/..."
                                    type="text"
                                    required
                                />
                                <button
                                    type="button"
                                    onClick={handlePaste}
                                    className="rounded-lg bg-primary/20 px-3 py-1.5 text-xs font-bold text-primary hover:bg-primary/30 transition-colors uppercase tracking-wider"
                                >
                                    Paste
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            <input
                                ref={fileInputRef}
                                type="file"
                                accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.mkv,.webm"
                                className="hidden"
                                onChange={(e) => {
                                    const f = e.target.files?.[0];
                                    if (f) {
                                        setSelectedFile(f);
                                        if (!uploadTitle) {
                                            setUploadTitle(f.name.replace(/\.[^.]+$/, ""));
                                        }
                                    }
                                }}
                            />
                            <div
                                onClick={() => fileInputRef.current?.click()}
                                onDragOver={(e) => e.preventDefault()}
                                onDrop={(e) => {
                                    e.preventDefault();
                                    const f = e.dataTransfer.files?.[0];
                                    if (f) {
                                        setSelectedFile(f);
                                        if (!uploadTitle) setUploadTitle(f.name.replace(/\.[^.]+$/, ""));
                                    }
                                }}
                                className="rounded-2xl border-2 border-dashed border-white/15 hover:border-primary/60 bg-white/[0.02] hover:bg-primary/5 p-8 text-center cursor-pointer transition-all"
                            >
                                <Upload className="h-8 w-8 text-primary mx-auto mb-2" />
                                {selectedFile ? (
                                    <div>
                                        <p className="text-sm font-bold text-white">{selectedFile.name}</p>
                                        <p className="text-xs text-slate-400 mt-1">
                                            {(selectedFile.size / (1024 * 1024)).toFixed(1)} MB · Click to change file
                                        </p>
                                    </div>
                                ) : (
                                    <div>
                                        <p className="text-sm font-bold text-white">Drop an MP4, MOV, MKV, or WEBM file here</p>
                                        <p className="text-xs text-slate-500 mt-1">Processed 100% locally on your machine</p>
                                    </div>
                                )}
                            </div>
                            {selectedFile && (
                                <input
                                    value={uploadTitle}
                                    onChange={(e) => setUploadTitle(e.target.value)}
                                    placeholder="Project title (optional)"
                                    className="w-full rounded-xl bg-slate-900/50 border border-slate-800 px-3.5 py-2.5 text-sm text-white focus:border-primary focus:outline-none"
                                />
                            )}
                        </div>
                    )}

                    {/* Clipping Options */}
                    <div className="space-y-4 pt-2 border-t border-white/10">
                        <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-slate-400">
                            <Sliders className="h-3.5 w-3.5 text-primary" />
                            Clip &amp; Styling Options
                        </div>

                        {/* Target Duration */}
                        <div>
                            <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">
                                Target Clip Length
                            </label>
                            <div className="grid grid-cols-3 gap-2">
                                {DURATION_PRESETS.map((p) => {
                                    const active = minDur === p.min && maxDur === p.max;
                                    return (
                                        <button
                                            key={p.label}
                                            type="button"
                                            onClick={() => {
                                                setMinDur(p.min);
                                                setMaxDur(p.max);
                                            }}
                                            className={`rounded-xl p-2.5 border text-left transition-all ${
                                                active
                                                    ? "border-primary bg-primary/15 text-white"
                                                    : "border-white/10 bg-slate-900/40 text-slate-400 hover:border-white/25"
                                            }`}
                                        >
                                            <p className="text-xs font-bold">{p.label}</p>
                                            <p className="text-[11px] opacity-75">{p.range}</p>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>

                        {/* Layout + Brand Kit */}
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            <div>
                                <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">
                                    9:16 Reframe Layout
                                </label>
                                <select
                                    value={layoutPref}
                                    onChange={(e) => setLayoutPref(e.target.value)}
                                    className="w-full rounded-xl bg-slate-900/60 border border-slate-800 px-3 py-2.5 text-sm text-slate-100 focus:border-primary focus:outline-none"
                                >
                                    {LAYOUT_PRESETS.map((l) => (
                                        <option key={l.value} value={l.value} className="bg-slate-900">
                                            {l.label}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">
                                    Brand Kit (Optional)
                                </label>
                                <select
                                    value={selectedBrandKit}
                                    onChange={(e) => setSelectedBrandKit(e.target.value)}
                                    className="w-full rounded-xl bg-slate-900/60 border border-slate-800 px-3 py-2.5 text-sm text-slate-100 focus:border-primary focus:outline-none"
                                >
                                    <option value="" className="bg-slate-900">Default Caption Style</option>
                                    {brandKits.map((k) => (
                                        <option key={k.id} value={k.id} className="bg-slate-900">
                                            {k.name} ({k.caption_style})
                                        </option>
                                    ))}
                                </select>
                            </div>
                        </div>

                        {/* Custom AI Focus Prompt */}
                        <div>
                            <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">
                                AI Clip Focus Prompt (Optional)
                            </label>
                            <input
                                value={customPrompt}
                                onChange={(e) => setCustomPrompt(e.target.value)}
                                placeholder="e.g. Find controversial hot takes, actionable tips, or funniest moments..."
                                className="w-full rounded-xl bg-slate-900/50 border border-slate-800 px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:border-primary focus:outline-none"
                            />
                            <div className="mt-2 flex flex-wrap gap-1.5">
                                {FOCUS_PRESETS.map((preset) => {
                                    const active = customPrompt.trim() === preset.prompt;
                                    return (
                                        <button
                                            key={preset.label}
                                            type="button"
                                            onClick={() => setCustomPrompt(active ? "" : preset.prompt)}
                                            className={`rounded-lg px-2.5 py-1 text-[11px] font-semibold border transition-all ${
                                                active
                                                    ? "border-primary bg-primary/20 text-white"
                                                    : "border-white/10 bg-slate-900/40 text-slate-400 hover:text-white hover:border-white/25"
                                            }`}
                                        >
                                            {preset.label}
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    </div>

                    {/* Provider + Model */}
                    <div className="space-y-3 pt-2 border-t border-white/10">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            <div>
                                <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                                    AI Provider
                                </label>
                                <div className="flex rounded-xl bg-slate-900/50 p-1 gap-1">
                                    {(["codex", "openai", "anthropic", "gemini", "ollama"] as const).map((p) => (
                                        <button
                                            key={p}
                                            type="button"
                                            onClick={() => {
                                                setProvider(p);
                                                if (p === "codex") setModel("");
                                                else if (p === "openai") setModel("gpt-4o");
                                                else if (p === "anthropic") setModel("claude-sonnet-5-5");
                                                else if (p === "gemini") setModel("gemini-3.5-flash");
                                                else if (p === "ollama") setModel("llama3");
                                            }}
                                            className={`flex-1 py-1.5 text-[11px] font-bold rounded-lg transition-all ${
                                                provider === p
                                                    ? "bg-primary text-white"
                                                    : "text-slate-400 hover:text-slate-200"
                                            }`}
                                        >
                                            {p === "codex" ? "Codex" : p === "openai" ? "OpenAI" : p === "anthropic" ? "Claude" : p === "gemini" ? "Gemini" : "Ollama"}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                                    Model {provider === "codex" ? "(Optional)" : ""}
                                </label>
                                <input
                                    value={model}
                                    onChange={(e) => setModel(e.target.value)}
                                    className="w-full rounded-xl bg-slate-900/50 border border-slate-800 px-3 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:border-primary focus:outline-none transition-all"
                                    placeholder={provider === "codex" ? "Default Codex subscription model" : "gpt-4o"}
                                />
                            </div>
                        </div>
                        {provider === "codex" && codexStatus && (
                            <div className={`rounded-xl px-3.5 py-2 text-xs flex items-center justify-between border ${
                                codexStatus.logged_in
                                    ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-300"
                                    : "bg-amber-500/10 border-amber-500/20 text-amber-300"
                            }`}>
                                <span>
                                    {codexStatus.logged_in
                                        ? `✓ Codex Subscription Active (${codexStatus.status_text})`
                                        : codexStatus.status_text}
                                </span>
                                {codexStatus.version && <span className="font-mono opacity-75">{codexStatus.version}</span>}
                            </div>
                        )}
                    </div>

                    {apiError && (
                        <div className="flex items-center gap-2 rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400">
                            {apiError}
                        </div>
                    )}

                    <button
                        type="submit"
                        disabled={isProcessing}
                        className="w-full rounded-xl bg-primary py-4 text-sm font-bold text-white glow-primary hover:bg-primary/90 transition-all disabled:opacity-50"
                    >
                        {isProcessing ? "Uploading & Starting…" : "Generate Clips"}
                    </button>
                </form>
            )}

            {/* Live Processing View */}
            {isLive && (
                <div className="space-y-6">
                    <div className="text-center mb-8">
                        <span className="mb-4 inline-flex items-center gap-2 rounded-full bg-primary/20 border border-primary/30 px-4 py-1 text-xs font-bold uppercase tracking-widest text-primary">
                            <span className="relative flex h-2 w-2">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
                                <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
                            </span>
                            Live Processing
                        </span>
                        <h1 className="text-4xl font-bold text-white mt-3">
                            Processing <span className="text-gradient">Video</span>
                        </h1>
                        <p className="text-slate-400 mt-2">Extracting highlights, reframing, and preparing Studio tracks.</p>
                    </div>

                    <div className="glass-card rounded-2xl p-8 space-y-6">
                        <div className="flex items-end justify-between">
                            <div>
                                <p className="text-slate-100 text-lg font-semibold">Overall Progress</p>
                                <p className="text-slate-400 text-sm mt-1">
                                    {STAGE_LABELS[stage] || message || "Processing…"}
                                </p>
                            </div>
                            <p className="text-primary text-3xl font-bold">{Math.round(clampedPercent)}%</p>
                        </div>
                        <div className="h-3 w-full bg-white/5 rounded-full overflow-hidden border border-white/5">
                            <div
                                className="h-full rounded-full transition-all duration-500"
                                style={{
                                    width: `${clampedPercent}%`,
                                    background: stage === "error" ? "#f87171" : "linear-gradient(to right, #2e1ded, #8b5cf6)",
                                }}
                            />
                        </div>
                    </div>

                    <div className="bg-[#0a0a0a] border border-accent-purple/30 rounded-xl p-5 font-mono text-xs leading-relaxed shadow-2xl">
                        <div className="space-y-1.5 h-48 overflow-y-auto custom-scrollbar">
                            {logs.length === 0 ? (
                                <p className="text-slate-500 italic">Connecting to pipeline…</p>
                            ) : (
                                logs.map((log, i) => (
                                    <p key={i} className="text-slate-300">
                                        {log}
                                    </p>
                                ))
                            )}
                            <div ref={logsEndRef} />
                        </div>
                    </div>

                    {projectId && (
                        <div className="flex justify-end">
                            <Link href={`/project/${projectId}`} className="text-slate-400 hover:text-white text-sm font-semibold">
                                View Project Page →
                            </Link>
                        </div>
                    )}
                </div>
            )}

            {/* Done — show clips with direct Studio Editor button */}
            {generatedClips.length > 0 && stage === "done" && (
                <div className="space-y-8">
                    <div className="text-center">
                        <h2 className="text-3xl font-bold text-white">
                            <span className="text-gradient">{generatedClips.length} Clips</span> Generated
                        </h2>
                        <p className="text-slate-400 mt-2">
                            Open any clip in the Studio Editor to customize captions, cuts, and branding.
                        </p>
                    </div>

                    <div className="grid grid-cols-1 gap-4">
                        {generatedClips.map((clip) => (
                            <div key={clip.id} className="glass rounded-2xl p-5 flex gap-5 hover:border-primary/30 transition-all">
                                <div className="relative w-20 aspect-[9/16] rounded-xl overflow-hidden flex-shrink-0 bg-slate-900 flex items-center justify-center">
                                    <span className="absolute top-1.5 left-1.5 rounded px-1.5 py-0.5 text-[10px] font-bold bg-primary text-white">
                                        {formatDuration(clip.duration)}
                                    </span>
                                    <Sparkles className="h-6 w-6 text-primary/50" />
                                </div>
                                <div className="flex-1 flex flex-col justify-between min-w-0">
                                    <div>
                                        <h4 className="text-sm font-bold text-white line-clamp-2">
                                            {clip.title || `Clip ${formatDuration(clip.start_time)} – ${formatDuration(clip.end_time)}`}
                                        </h4>
                                        {clip.reason && (
                                            <p className="text-xs text-slate-400 mt-1 line-clamp-2">{clip.reason}</p>
                                        )}
                                    </div>
                                    <div className="flex gap-2 mt-3">
                                        <Link
                                            href={`/project/${projectId}/edit/${clip.id}`}
                                            className="flex-1 py-2 rounded-lg bg-primary hover:bg-primary/90 text-[11px] font-bold text-center text-white transition-colors"
                                        >
                                            OPEN IN EDITOR
                                        </Link>
                                        <a
                                            href={`${getApiBaseUrl()}${clip.file_path}`}
                                            download
                                            className="px-4 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-[11px] font-bold text-center border border-white/10"
                                        >
                                            DOWNLOAD
                                        </a>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>

                    <button
                        onClick={() => router.push(`/project/${projectId}`)}
                        className="w-full rounded-xl glass glass-hover py-4 text-sm font-bold text-white transition-all"
                    >
                        View Full Project →
                    </button>
                </div>
            )}
        </div>
    );
}
