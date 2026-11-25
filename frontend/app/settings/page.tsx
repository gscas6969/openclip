"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import {
    getSettings,
    saveSettings,
    getEncoderStatus,
    getCaptionStyles,
    getBrandKits,
    createBrandKit,
    deleteBrandKit,
    uploadLogoAsset,
    getMcpConfig,
    getCodexStatus,
    getYouTubeStatus,
    uploadYouTubeClientSecretFile,
    getYouTubeAuthUrl,
    disconnectYouTube,
    getStorageStats,
    cleanupStorage,
    getApiBaseUrl,
} from "@/lib/api";
import { BrandKit, CodexStatus, Settings, CaptionStyle, McpConfigInfo, YouTubeStatus, StorageStats, EncoderStatus } from "@/lib/types";
import { Trash2, Upload, Plus, Copy, Check, Terminal, Youtube, HardDrive, Cpu } from "lucide-react";

function formatSize(bytes: number): string {
    if (!bytes) return "0 MB";
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const WHISPER_OPTIONS = [
    { value: "base", label: "Base (Fast)", desc: "Lowest latency" },
    { value: "small", label: "Small (Balanced)", desc: "Optimal performance" },
    { value: "medium", label: "Medium (Accurate)", desc: "Highest precision" },
] as const;

export default function SettingsPage() {
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [savedStatus, setSavedStatus] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const [provider, setProvider] = useState<Settings["llm_provider"]>("openai");
    const [model, setModel] = useState("gpt-4o");
    const [whisperModel, setWhisperModel] = useState<Settings["whisper_model"]>("base");
    const [videoEncoder, setVideoEncoder] = useState<string>("auto");
    const [encoderStatus, setEncoderStatus] = useState<EncoderStatus | null>(null);
    const [captionStyle, setCaptionStyle] = useState("mozi_pop");
    const [captionStylesList, setCaptionStylesList] = useState<CaptionStyle[]>([]);
    const [presetCat, setPresetCat] = useState<"all" | "kinetic" | "clean" | "vibrant">("all");
    const [apiKey, setApiKey] = useState("");
    const [hasApiKey, setHasApiKey] = useState(false);
    const [editingKey, setEditingKey] = useState(false);

    // Brand Kits state
    const [brandKits, setBrandKits] = useState<BrandKit[]>([]);
    const [kitName, setKitName] = useState("");
    const [kitLogoUrl, setKitLogoUrl] = useState<string | null>(null);
    const [kitStyle, setKitStyle] = useState("mozi_pop");
    const [uploadingLogo, setUploadingLogo] = useState(false);
    const [mcpConfig, setMcpConfig] = useState<McpConfigInfo | null>(null);
    const [mcpFormat, setMcpFormat] = useState<"codex" | "json">("codex");
    const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null);
    const [ytStatus, setYtStatus] = useState<YouTubeStatus | null>(null);
    const [ytMsg, setYtMsg] = useState<string | null>(null);
    const [ytBusy, setYtBusy] = useState(false);
    const [storage, setStorage] = useState<StorageStats | null>(null);
    const [storageBusy, setStorageBusy] = useState<string | null>(null);
    const [storageMsg, setStorageMsg] = useState<string | null>(null);
    const [copiedRedirect, setCopiedRedirect] = useState(false);
    const [copiedMcp, setCopiedMcp] = useState(false);
    const logoRef = useRef<HTMLInputElement>(null);
    const ytSecretRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (typeof window !== "undefined") {
            const p = new URLSearchParams(window.location.search).get("youtube");
            if (p === "connected") setYtMsg("✓ YouTube channel connected!");
            else if (p === "error") setYtMsg("YouTube OAuth failed — double-check your Redirect URI & Test User in Google Cloud.");
        }
        Promise.all([getSettings(), getCaptionStyles(), getBrandKits(), getMcpConfig(), getCodexStatus(), getYouTubeStatus(), getStorageStats(), getEncoderStatus()])
            .then(([data, styles, kits, mcp, cx, yt, st, enc]) => {
                setProvider(data.llm_provider ?? "codex");
                setModel(data.llm_model ?? "");
                setWhisperModel(data.whisper_model ?? "base");
                setVideoEncoder(data.video_encoder || "auto");
                setCaptionStyle(data.caption_style || "mozi_pop");
                setHasApiKey(Boolean(data.has_api_key));
                setCaptionStylesList(styles);
                setBrandKits(kits);
                setMcpConfig(mcp);
                setCodexStatus(cx);
                setYtStatus(yt);
                setStorage(st);
                setEncoderStatus(enc);
            })
            .catch(() => setError("Failed to load settings."))
            .finally(() => setLoading(false));
    }, []);

    const handleProviderChange = (p: Settings["llm_provider"]) => {
        setProvider(p);
        if (p === "codex") setModel("");
        if (p === "openai") setModel("gpt-4o");
        if (p === "anthropic") setModel("claude-sonnet-5-5");
        if (p === "gemini") setModel("gemini-3.5-flash");
        if (p === "ollama") setModel("llama3");
    };

    const handleSave = async (e: React.FormEvent) => {
        e.preventDefault();
        setSaving(true);
        setError(null);
        setSavedStatus(null);
        try {
            const payload: Partial<Settings> & { llm_api_key?: string } = {
                llm_provider: provider,
                llm_model: model,
                whisper_model: whisperModel,
                caption_style: captionStyle,
                video_encoder: videoEncoder,
            };
            if (editingKey || (!hasApiKey && apiKey)) payload.llm_api_key = apiKey;
            await saveSettings(payload);
            const updatedEnc = await getEncoderStatus();
            if (updatedEnc) setEncoderStatus(updatedEnc);
            setSavedStatus("Settings saved!");
            if (payload.llm_api_key) setHasApiKey(true);
            setEditingKey(false);
            setApiKey("");
            setTimeout(() => setSavedStatus(null), 3000);
        } catch {
            setError("Failed to save settings.");
        } finally {
            setSaving(false);
        }
    };

    const handleCreateBrandKit = async () => {
        if (!kitName.trim()) return;
        try {
            const created = await createBrandKit({
                name: kitName.trim(),
                logo_path: kitLogoUrl,
                caption_style: kitStyle,
                caption_overrides: {},
                logo_config: { enabled: Boolean(kitLogoUrl), x: 84, y: 7, scale: 18, opacity: 92 },
            });
            setBrandKits((prev) => [created, ...prev]);
            setKitName("");
            setKitLogoUrl(null);
        } catch {
            setError("Failed to create Brand Kit.");
        }
    };

    const visibleStyles = captionStylesList.filter((s) =>
        s.key === "none" || presetCat === "all" ? true : s.category === presetCat,
    );
    const baseUrl = getApiBaseUrl();

    return (
        <div className="flex h-[calc(100vh-73px)] overflow-hidden">
            <main className="flex-1 flex flex-col overflow-y-auto custom-scrollbar">
                <header className="sticky top-0 z-10 flex items-center gap-4 px-10 py-5 border-b border-white/5 glass-dark">
                    <Link href="/" className="flex h-9 w-9 items-center justify-center rounded-xl glass glass-hover text-slate-400 hover:text-white">
                        ←
                    </Link>
                    <h1 className="text-xl font-bold text-slate-100">Settings &amp; Brand Kits</h1>
                </header>

                {loading ? (
                    <div className="flex-1 flex items-center justify-center">
                        <div className="h-8 w-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                    </div>
                ) : (
                    <div className="max-w-3xl mx-auto w-full p-10 space-y-12">
                        <form onSubmit={handleSave} className="space-y-10">
                            {/* LLM Provider */}
                            <section className="space-y-4">
                                <h2 className="text-lg font-bold text-slate-100">LLM Provider</h2>
                                <div className="flex p-1 bg-slate-900/50 rounded-xl gap-1">
                                    {(["codex", "openai", "anthropic", "gemini", "ollama"] as const).map((p) => (
                                        <button
                                            key={p}
                                            type="button"
                                            onClick={() => handleProviderChange(p)}
                                            className={`flex-1 py-2 text-sm font-bold rounded-lg transition-all ${
                                                provider === p ? "bg-primary text-white" : "text-slate-400 hover:text-slate-100"
                                            }`}
                                        >
                                            {p === "codex" ? "Codex" : p === "openai" ? "OpenAI" : p === "anthropic" ? "Anthropic" : p === "gemini" ? "Gemini" : "Ollama"}
                                        </button>
                                    ))}
                                </div>
                                {provider === "codex" && codexStatus && (
                                    <div className={`p-3.5 rounded-xl border text-sm flex items-center justify-between ${
                                        codexStatus.logged_in
                                            ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-300"
                                            : "bg-amber-500/10 border-amber-500/20 text-amber-300"
                                    }`}>
                                        <span>
                                            {codexStatus.logged_in
                                                ? `✓ ${codexStatus.status_text} (uses your Codex subscription — no API key needed)`
                                                : codexStatus.status_text}
                                        </span>
                                        {codexStatus.version && <span className="font-mono text-xs opacity-75">{codexStatus.version}</span>}
                                    </div>
                                )}
                                {provider !== "ollama" && provider !== "codex" && (
                                    <div>
                                        <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                                            API Key
                                        </label>
                                        {hasApiKey && !editingKey ? (
                                            <div className="flex items-center justify-between p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm">
                                                <span>✓ API key saved</span>
                                                <button type="button" onClick={() => setEditingKey(true)} className="text-xs font-bold hover:underline">
                                                    Change
                                                </button>
                                            </div>
                                        ) : (
                                            <input
                                                type="password"
                                                value={apiKey}
                                                onChange={(e) => setApiKey(e.target.value)}
                                                placeholder="Paste API key..."
                                                className="w-full bg-slate-900/50 border border-slate-800 rounded-xl px-4 py-3 text-slate-100 focus:border-primary focus:outline-none text-sm"
                                            />
                                        )}
                                    </div>
                                )}
                                <div>
                                    <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                                        Model Name {provider === "codex" ? "(Leave blank for Codex default)" : ""}
                                    </label>
                                    <input
                                        value={model}
                                        onChange={(e) => setModel(e.target.value)}
                                        placeholder={provider === "codex" ? "Auto (Codex subscription default)" : "gpt-4o"}
                                        className="w-full bg-slate-900/50 border border-slate-800 rounded-xl px-4 py-3 text-slate-100 placeholder-slate-500 focus:border-primary focus:outline-none text-sm"
                                    />
                                </div>
                            </section>

                            {/* Whisper */}
                            <section className="space-y-4">
                                <h2 className="text-lg font-bold text-slate-100">Whisper Model (Word-Level Timestamps)</h2>
                                <div className="grid grid-cols-3 gap-4">
                                    {WHISPER_OPTIONS.map((opt) => (
                                        <button
                                            key={opt.value}
                                            type="button"
                                            onClick={() => setWhisperModel(opt.value)}
                                            className={`p-4 rounded-xl text-left border transition-all ${
                                                whisperModel === opt.value
                                                    ? "border-[var(--success)] bg-[var(--success)]/5"
                                                    : "border-slate-800 bg-slate-900/30 hover:border-slate-700"
                                            }`}
                                        >
                                            <p className="font-bold text-slate-100 text-sm">{opt.label}</p>
                                            <p className="text-xs text-slate-500 mt-1">{opt.desc}</p>
                                        </button>
                                    ))}
                                </div>
                            </section>

                            {/* Hardware Video Encoding */}
                            <section className="space-y-4">
                                <div className="flex items-center justify-between flex-wrap gap-2">
                                    <div className="flex items-center gap-2">
                                        <Cpu className="h-4 w-4 text-primary" />
                                        <h2 className="text-lg font-bold text-slate-100">Video Encoder (GPU Acceleration)</h2>
                                    </div>
                                    {encoderStatus && (
                                        <span
                                            className={`px-2.5 py-1 rounded-full text-xs font-bold border ${
                                                encoderStatus.active !== "libx264"
                                                    ? "bg-emerald-500/10 border-emerald-500/25 text-emerald-300"
                                                    : "bg-slate-800/80 border-slate-700 text-slate-300"
                                            }`}
                                        >
                                            Active: {encoderStatus.active_label}
                                        </span>
                                    )}
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                                    <button
                                        type="button"
                                        onClick={() => setVideoEncoder("auto")}
                                        className={`p-4 rounded-xl text-left border transition-all ${
                                            videoEncoder === "auto"
                                                ? "border-[var(--success)] bg-[var(--success)]/5"
                                                : "border-slate-800 bg-slate-900/30 hover:border-slate-700"
                                        }`}
                                    >
                                        <p className="font-bold text-slate-100 text-sm">Auto (Recommended)</p>
                                        <p className="text-xs text-slate-500 mt-1">
                                            {encoderStatus?.gpu_available
                                                ? `Uses ${encoderStatus.available.find((e) => e.kind === "gpu")?.label} with CPU fallback`
                                                : "Uses GPU when detected, else CPU"}
                                        </p>
                                    </button>
                                    {(encoderStatus?.available || [])
                                        .filter((e) => e.kind === "gpu")
                                        .map((opt) => (
                                            <button
                                                key={opt.id}
                                                type="button"
                                                onClick={() => setVideoEncoder(opt.id)}
                                                className={`p-4 rounded-xl text-left border transition-all ${
                                                    videoEncoder === opt.id
                                                        ? "border-[var(--success)] bg-[var(--success)]/5"
                                                        : "border-slate-800 bg-slate-900/30 hover:border-slate-700"
                                                }`}
                                            >
                                                <p className="font-bold text-slate-100 text-sm">{opt.label}</p>
                                                <p className="text-xs text-slate-500 mt-1">Hardware GPU ({opt.id})</p>
                                            </button>
                                        ))}
                                    <button
                                        type="button"
                                        onClick={() => setVideoEncoder("cpu")}
                                        className={`p-4 rounded-xl text-left border transition-all ${
                                            videoEncoder === "cpu"
                                                ? "border-[var(--success)] bg-[var(--success)]/5"
                                                : "border-slate-800 bg-slate-900/30 hover:border-slate-700"
                                        }`}
                                    >
                                        <p className="font-bold text-slate-100 text-sm">CPU Only (libx264)</p>
                                        <p className="text-xs text-slate-500 mt-1">Software encoding fallback</p>
                                    </button>
                                </div>
                            </section>

                            {/* Default Caption Style */}
                            <section className="space-y-4">
                                <div className="flex items-center justify-between flex-wrap gap-2">
                                    <h2 className="text-lg font-bold text-slate-100">
                                        Default Caption Preset ({captionStylesList.length - 1})
                                    </h2>
                                    <div className="flex gap-1 bg-slate-900/60 p-1 rounded-lg">
                                        {(["all", "kinetic", "clean", "vibrant"] as const).map((cat) => (
                                            <button
                                                key={cat}
                                                type="button"
                                                onClick={() => setPresetCat(cat)}
                                                className={`px-3 py-1 rounded-md text-xs font-bold capitalize ${
                                                    presetCat === cat ? "bg-primary text-white" : "text-slate-400 hover:text-white"
                                                }`}
                                            >
                                                {cat}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                    {visibleStyles.map((style) => (
                                        <button
                                            key={style.key}
                                            type="button"
                                            onClick={() => setCaptionStyle(style.key)}
                                            className="space-y-1.5 text-left"
                                        >
                                            <div
                                                className={`aspect-video rounded-xl flex flex-col items-center justify-center border-2 transition-all px-2 ${
                                                    captionStyle === style.key
                                                        ? "border-[var(--success)] bg-[var(--success)]/10"
                                                        : "border-slate-800 bg-slate-900/50 hover:border-slate-600"
                                                }`}
                                                style={{ backgroundColor: style.preview_colors.background || undefined }}
                                            >
                                                {style.key === "none" ? (
                                                    <span className="text-slate-500 font-bold line-through text-xs">OFF</span>
                                                ) : (
                                                    <span className="font-black text-xs uppercase" style={{ color: style.preview_colors.text || "#fff" }}>
                                                        THE{" "}
                                                        <span style={{ color: style.preview_colors.highlight || "#FFE600" }}>
                                                            HOOK
                                                        </span>
                                                    </span>
                                                )}
                                                {style.key !== "none" && (
                                                    <span className="text-[9px] text-slate-400 uppercase mt-1">{style.animation}</span>
                                                )}
                                            </div>
                                            <p className={`text-center text-xs font-bold ${captionStyle === style.key ? "text-[var(--success)]" : "text-slate-400"}`}>
                                                {style.name}
                                            </p>
                                        </button>
                                    ))}
                                </div>
                            </section>

                            {error && (
                                <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400">
                                    {error}
                                </div>
                            )}

                            <button
                                type="submit"
                                disabled={saving}
                                className="w-full bg-primary hover:bg-primary/90 text-white py-4 rounded-xl font-bold transition-all glow-primary disabled:opacity-50"
                            >
                                {saving ? "Saving…" : savedStatus ? `✓ ${savedStatus}` : "Save Settings"}
                            </button>
                        </form>

                        {/* Brand Kits Manager */}
                        <section className="space-y-4 pt-8 border-t border-white/10">
                            <div>
                                <h2 className="text-lg font-bold text-slate-100">Brand Kits</h2>
                                <p className="text-xs text-slate-400 mt-1">
                                    Combine a logo watermark and caption style to apply automatically to new projects or inside the Studio Editor.
                                </p>
                            </div>

                            <div className="glass-card rounded-2xl p-5 space-y-4">
                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                                    <input
                                        value={kitName}
                                        onChange={(e) => setKitName(e.target.value)}
                                        placeholder="Brand Kit Name (e.g. Podcast Main)"
                                        className="rounded-xl bg-slate-900/60 border border-slate-800 px-3.5 py-2.5 text-sm text-white focus:border-primary focus:outline-none"
                                    />
                                    <select
                                        value={kitStyle}
                                        onChange={(e) => setKitStyle(e.target.value)}
                                        className="rounded-xl bg-slate-900/60 border border-slate-800 px-3 py-2.5 text-sm text-white focus:border-primary focus:outline-none"
                                    >
                                        {captionStylesList.map((s) => (
                                            <option key={s.key} value={s.key} className="bg-slate-900">
                                                {s.name}
                                            </option>
                                        ))}
                                    </select>
                                    <div className="flex gap-2">
                                        <input
                                            ref={logoRef}
                                            type="file"
                                            accept="image/png,image/jpeg,image/webp"
                                            className="hidden"
                                            onChange={async (e) => {
                                                const f = e.target.files?.[0];
                                                if (!f) return;
                                                setUploadingLogo(true);
                                                try {
                                                    setKitLogoUrl(await uploadLogoAsset(f));
                                                } finally {
                                                    setUploadingLogo(false);
                                                }
                                            }}
                                        />
                                        <button
                                            type="button"
                                            onClick={() => logoRef.current?.click()}
                                            className="flex-1 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-xs font-bold text-slate-200 flex items-center justify-center gap-1.5"
                                        >
                                            <Upload className="h-3.5 w-3.5" />
                                            {uploadingLogo ? "…" : kitLogoUrl ? "Logo Ready" : "Logo"}
                                        </button>
                                        <button
                                            type="button"
                                            onClick={handleCreateBrandKit}
                                            className="px-4 rounded-xl bg-primary hover:bg-primary/90 text-xs font-bold text-white flex items-center gap-1"
                                        >
                                            <Plus className="h-3.5 w-3.5" />
                                            Add
                                        </button>
                                    </div>
                                </div>

                                {brandKits.length > 0 && (
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
                                        {brandKits.map((kit) => (
                                            <div key={kit.id} className="rounded-xl bg-white/[0.03] border border-white/10 p-3 flex items-center justify-between gap-3">
                                                <div className="flex items-center gap-3 min-w-0">
                                                    {kit.logo_path ? (
                                                        <img src={`${baseUrl}${kit.logo_path}`} alt={kit.name} className="h-9 w-9 object-contain rounded bg-black/50 p-1" />
                                                    ) : (
                                                        <div className="h-9 w-9 rounded bg-primary/20 flex items-center justify-center text-xs font-black text-indigo-300">
                                                            {kit.name.slice(0, 2).toUpperCase()}
                                                        </div>
                                                    )}
                                                    <div className="min-w-0">
                                                        <p className="text-sm font-bold text-white truncate">{kit.name}</p>
                                                        <p className="text-xs text-slate-500">Preset: {kit.caption_style}</p>
                                                    </div>
                                                </div>
                                                <button
                                                    type="button"
                                                    onClick={async () => {
                                                        await deleteBrandKit(kit.id);
                                                        setBrandKits((prev) => prev.filter((k) => k.id !== kit.id));
                                                    }}
                                                    className="p-2 text-slate-500 hover:text-red-400"
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </section>

                        {/* YouTube Auto-Post & Scheduler */}
                        <section id="youtube" className="space-y-4 pt-8 border-t border-white/10">
                            <div className="flex items-center justify-between gap-4 flex-wrap">
                                <div>
                                    <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                                        <Youtube className="h-5 w-5 text-red-500" />
                                        YouTube Shorts Auto-Post &amp; Scheduler
                                    </h2>
                                    <p className="text-xs text-slate-400 mt-1">
                                        Connect your YouTube channel via Google&apos;s official YouTube Data API v3 (tokens stay encrypted locally).
                                    </p>
                                </div>
                                {ytStatus?.connected && (
                                    <span className="px-3 py-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-xs font-bold text-emerald-300">
                                        ✓ Connected
                                    </span>
                                )}
                            </div>

                            {ytMsg && (
                                <div className={`rounded-xl px-4 py-2.5 text-xs font-semibold border ${
                                    ytMsg.startsWith("✓")
                                        ? "bg-emerald-500/10 border-emerald-500/25 text-emerald-300"
                                        : "bg-red-500/10 border-red-500/25 text-red-300"
                                }`}>
                                    {ytMsg}
                                </div>
                            )}

                            <div className="glass-card rounded-2xl p-5 space-y-4">
                                {ytStatus?.connected ? (
                                    <div className="flex items-center justify-between gap-4 flex-wrap">
                                        <div className="flex items-center gap-3">
                                            {ytStatus.channel_thumbnail ? (
                                                <img src={ytStatus.channel_thumbnail} alt="Channel" className="h-11 w-11 rounded-full border border-white/15" />
                                            ) : (
                                                <div className="h-11 w-11 rounded-full bg-red-600/20 flex items-center justify-center text-red-400">
                                                    <Youtube className="h-5 w-5" />
                                                </div>
                                            )}
                                            <div>
                                                <p className="text-sm font-bold text-white">{ytStatus.channel_title || "Connected YouTube Channel"}</p>
                                                <p className="text-xs text-slate-400">Ready for 1-click Short uploads &amp; auto-scheduling (up to 6/day free quota)</p>
                                            </div>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={async () => {
                                                await disconnectYouTube();
                                                setYtStatus(await getYouTubeStatus());
                                                setYtMsg("Disconnected YouTube channel.");
                                            }}
                                            className="px-4 py-2 rounded-xl bg-white/5 hover:bg-red-500/20 border border-white/10 text-xs font-bold text-slate-300 hover:text-red-300"
                                        >
                                            Disconnect
                                        </button>
                                    </div>
                                ) : (
                                    <div className="space-y-4">
                                        <div className="rounded-xl bg-slate-950/70 border border-white/10 p-4 space-y-2 text-xs text-slate-300">
                                            <p className="font-bold text-white">One-Time 5-Minute Setup (Free):</p>
                                            <ol className="list-decimal list-inside space-y-1 text-slate-400">
                                                <li>Open <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noreferrer" className="text-indigo-300 underline">Google Cloud Console</a>, create a project &amp; enable <strong>YouTube Data API v3</strong>.</li>
                                                <li>Under <strong>OAuth consent screen</strong>, add your Gmail under <em>Test Users</em>.</li>
                                                <li>Under <strong>Credentials → Create OAuth Client ID (Web application)</strong>, add this exact Authorized Redirect URI:</li>
                                            </ol>
                                            <div className="flex items-center gap-2 pt-1">
                                                <code className="flex-1 rounded-lg bg-black/70 border border-white/10 px-3 py-1.5 font-mono text-[11px] text-indigo-200">
                                                    {ytStatus?.redirect_uri || "http://localhost:8000/api/youtube/callback"}
                                                </code>
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        navigator.clipboard.writeText(ytStatus?.redirect_uri || "http://localhost:8000/api/youtube/callback");
                                                        setCopiedRedirect(true);
                                                        setTimeout(() => setCopiedRedirect(false), 2000);
                                                    }}
                                                    className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/15 text-[11px] font-bold text-white flex items-center gap-1"
                                                >
                                                    {copiedRedirect ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                                                    {copiedRedirect ? "Copied" : "Copy"}
                                                </button>
                                            </div>
                                            <p className="text-slate-400 pt-1">4. Download the JSON file and upload it below, then click <strong>Connect YouTube Channel</strong>.</p>
                                        </div>

                                        <input
                                            ref={ytSecretRef}
                                            type="file"
                                            accept=".json,application/json"
                                            className="hidden"
                                            onChange={async (e) => {
                                                const f = e.target.files?.[0];
                                                if (!f) return;
                                                setYtBusy(true);
                                                setYtMsg(null);
                                                try {
                                                    await uploadYouTubeClientSecretFile(f);
                                                    setYtStatus(await getYouTubeStatus());
                                                    setYtMsg("✓ client_secret.json saved (encrypted). Now click Connect YouTube Channel!");
                                                } catch (err) {
                                                    setYtMsg(err instanceof Error ? err.message : "Invalid JSON file");
                                                } finally {
                                                    setYtBusy(false);
                                                }
                                            }}
                                        />

                                        <div className="flex flex-wrap gap-3">
                                            <button
                                                type="button"
                                                disabled={ytBusy}
                                                onClick={() => ytSecretRef.current?.click()}
                                                className="px-4 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 border border-white/15 text-xs font-bold text-white flex items-center gap-2"
                                            >
                                                <Upload className="h-4 w-4" />
                                                {ytStatus?.has_client_secret ? "Replace client_secret.json" : "Upload client_secret.json"}
                                            </button>

                                            <button
                                                type="button"
                                                disabled={!ytStatus?.has_client_secret || ytBusy}
                                                onClick={async () => {
                                                    setYtBusy(true);
                                                    try {
                                                        const { auth_url } = await getYouTubeAuthUrl();
                                                        window.location.href = auth_url;
                                                    } catch (err) {
                                                        setYtMsg(err instanceof Error ? err.message : "OAuth failed");
                                                        setYtBusy(false);
                                                    }
                                                }}
                                                className="px-5 py-2.5 rounded-xl bg-red-600 hover:bg-red-500 disabled:opacity-40 text-xs font-bold text-white flex items-center gap-2 transition-colors"
                                            >
                                                <Youtube className="h-4 w-4" />
                                                Connect YouTube Channel
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </section>

                        {/* Storage & Video Cleanup */}
                        <section id="storage" className="space-y-4 pt-8 border-t border-white/10">
                            <div className="flex items-center justify-between gap-4 flex-wrap">
                                <div>
                                    <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                                        <HardDrive className="h-5 w-5 text-cyan-400" />
                                        Storage &amp; Video Cleanup
                                    </h2>
                                    <p className="text-xs text-slate-400 mt-1">
                                        Reclaim disk space by removing large downloaded/uploaded source videos, superseded exports, Quick Tools files, or failed projects.
                                    </p>
                                </div>
                                {storage && (
                                    <span className="px-3 py-1 rounded-full bg-cyan-500/15 border border-cyan-500/30 text-xs font-mono font-bold text-cyan-300">
                                        Total Used: {formatSize(storage.total_bytes)}
                                    </span>
                                )}
                            </div>

                            {storageMsg && (
                                <div className="rounded-xl px-4 py-2.5 text-xs font-semibold bg-emerald-500/10 border border-emerald-500/25 text-emerald-300">
                                    {storageMsg}
                                </div>
                            )}

                            {storage && (
                                <div className="glass-card rounded-2xl p-5 space-y-5">
                                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                        {[
                                            { label: "Source Videos", size: storage.source_videos_bytes, sub: `${storage.source_videos_count} file(s)`, target: "sources" as const, btn: "Delete Done Sources" },
                                            { label: "Temp & Old Exports", size: storage.orphan_bytes, sub: `${storage.orphan_count} file(s)`, target: "orphans" as const, btn: "Clean Temp Files" },
                                            { label: "Quick Tools", size: storage.tools_bytes, sub: `${storage.tools_count} file(s)`, target: "tools" as const, btn: "Clear Tools" },
                                            { label: "Failed Projects", size: storage.failed_projects_bytes, sub: `${storage.failed_projects_count} project(s)`, target: "failed_projects" as const, btn: "Purge Failed" },
                                        ].map((card) => (
                                            <div key={card.label} className="rounded-xl bg-white/[0.03] border border-white/10 p-3.5 flex flex-col justify-between gap-3">
                                                <div>
                                                    <p className="text-[11px] text-slate-400 font-semibold">{card.label}</p>
                                                    <p className="text-base font-black text-white mt-0.5">{formatSize(card.size)}</p>
                                                    <p className="text-[10px] text-slate-500">{card.sub}</p>
                                                </div>
                                                <button
                                                    type="button"
                                                    disabled={card.size === 0 || storageBusy !== null}
                                                    onClick={async () => {
                                                        setStorageBusy(card.target);
                                                        setStorageMsg(null);
                                                        try {
                                                            const res = await cleanupStorage(card.target);
                                                            setStorage(res.stats);
                                                            setStorageMsg(`✓ Freed ${formatSize(res.freed_bytes)} (${res.removed_files} item(s) removed).`);
                                                        } finally {
                                                            setStorageBusy(null);
                                                        }
                                                    }}
                                                    className="w-full py-1.5 rounded-lg bg-white/10 hover:bg-red-500/25 disabled:opacity-35 text-[11px] font-bold text-slate-200 hover:text-red-200 transition-colors"
                                                >
                                                    {storageBusy === card.target ? "Cleaning…" : card.btn}
                                                </button>
                                            </div>
                                        ))}
                                    </div>

                                    {storage.projects.length > 0 && (
                                        <div className="space-y-2 pt-3 border-t border-white/10">
                                            <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                                                Per-Project Storage (Finished clips: {formatSize(storage.clips_bytes)})
                                            </p>
                                            {storage.projects.map((p) => (
                                                <div key={p.id} className="rounded-xl bg-white/[0.02] border border-white/5 px-3.5 py-2.5 flex items-center justify-between gap-3 text-xs">
                                                    <div className="min-w-0">
                                                        <p className="font-bold text-white truncate">{p.title}</p>
                                                        <p className="text-[11px] text-slate-500">
                                                            Source: {formatSize(p.source_bytes)} · Clips: {formatSize(p.clips_bytes)} ({p.clip_count} clips) · Status: {p.status}
                                                        </p>
                                                    </div>
                                                    {p.has_source && p.status === "done" && (
                                                        <button
                                                            type="button"
                                                            disabled={storageBusy !== null}
                                                            onClick={async () => {
                                                                setStorageBusy(p.id);
                                                                try {
                                                                    const res = await cleanupStorage("project_source", p.id);
                                                                    setStorage(res.stats);
                                                                    setStorageMsg(`✓ Deleted source video for "${p.title}" — freed ${formatSize(res.freed_bytes)} (clips kept!).`);
                                                                } finally {
                                                                    setStorageBusy(null);
                                                                }
                                                            }}
                                                            className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-red-500/20 border border-white/10 text-[11px] font-bold text-slate-300 hover:text-red-300 flex-shrink-0"
                                                        >
                                                            {storageBusy === p.id ? "…" : `Delete Source (${formatSize(p.source_bytes)})`}
                                                        </button>
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}
                        </section>

                        {/* MCP Server Integration */}
                        <section className="space-y-4 pt-8 border-t border-white/10 pb-8">
                            <div className="flex items-center justify-between gap-4 flex-wrap">
                                <div>
                                    <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                                        <Terminal className="h-5 w-5 text-primary" />
                                        AI Assistant Integration (Local MCP Server)
                                    </h2>
                                    <p className="text-xs text-slate-400 mt-1">
                                        Connect Claude Desktop, Cursor, or Antigravity to trigger clipping, batch queues, layout changes, and renders directly from chat.
                                    </p>
                                </div>
                                {mcpConfig && (
                                    <div className="flex items-center gap-2">
                                        <div className="flex p-1 bg-slate-900/60 rounded-xl gap-1">
                                            <button
                                                type="button"
                                                onClick={() => setMcpFormat("codex")}
                                                className={`px-3 py-1 rounded-lg text-xs font-bold ${
                                                    mcpFormat === "codex" ? "bg-primary text-white" : "text-slate-400"
                                                }`}
                                            >
                                                Codex (TOML)
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => setMcpFormat("json")}
                                                className={`px-3 py-1 rounded-lg text-xs font-bold ${
                                                    mcpFormat === "json" ? "bg-primary text-white" : "text-slate-400"
                                                }`}
                                            >
                                                JSON
                                            </button>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={() => {
                                                const txt =
                                                    mcpFormat === "codex" && mcpConfig.codex_toml
                                                        ? mcpConfig.codex_toml
                                                        : mcpConfig.config_json;
                                                navigator.clipboard.writeText(txt);
                                                setCopiedMcp(true);
                                                setTimeout(() => setCopiedMcp(false), 2500);
                                            }}
                                            className="px-3.5 py-2 rounded-xl bg-primary/20 hover:bg-primary/30 border border-primary/40 text-xs font-bold text-indigo-300 flex items-center gap-1.5"
                                        >
                                            {copiedMcp ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                                            {copiedMcp ? "Copied!" : `Copy ${mcpFormat === "codex" ? "TOML" : "JSON"}`}
                                        </button>
                                    </div>
                                )}
                            </div>

                            {mcpConfig && (
                                <div className="glass-card rounded-2xl p-5 space-y-4">
                                    <p className="text-[11px] text-slate-400">
                                        {mcpFormat === "codex"
                                            ? "Paste into ~/.codex/config.toml so Codex in VS Code & CLI can control OpenClip Studio:"
                                            : "Paste into Claude Desktop, Cursor, or Antigravity MCP settings:"}
                                    </p>
                                    <pre className="rounded-xl bg-[#07070b] border border-white/10 p-4 text-xs font-mono text-indigo-200 overflow-x-auto">
                                        {mcpFormat === "codex" && mcpConfig.codex_toml
                                            ? mcpConfig.codex_toml
                                            : mcpConfig.config_json}
                                    </pre>
                                    <div>
                                        <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                                            Exposed MCP Tools ({mcpConfig.tools.length})
                                        </p>
                                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                            {mcpConfig.tools.map((t) => (
                                                <div key={t.name} className="rounded-lg bg-white/[0.03] border border-white/5 p-2.5">
                                                    <p className="text-xs font-mono font-bold text-white">{t.name}</p>
                                                    <p className="text-[11px] text-slate-400 mt-0.5 line-clamp-2">{t.description}</p>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            )}
                        </section>
                    </div>
                )}
            </main>
        </div>
    );
}
