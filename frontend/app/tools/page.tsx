"use client";

import { useRef, useState } from "react";
import {
    getApiBaseUrl,
    runQuickCompress,
    runQuickConvert,
    runQuickTransform,
    runQuickTrim,
} from "@/lib/api";
import { QuickToolResult } from "@/lib/types";
import {
    Scissors,
    Minimize2,
    RefreshCw,
    RotateCw,
    Upload,
    Download,
    Loader2,
    Wrench,
    CheckCircle2,
} from "lucide-react";

type ToolTab = "trim" | "compress" | "convert" | "transform";

function formatBytes(bytes: number): string {
    if (!bytes) return "0 B";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export default function QuickToolsPage() {
    const [activeTool, setActiveTool] = useState<ToolTab>("trim");
    const [file, setFile] = useState<File | null>(null);
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [fileDuration, setFileDuration] = useState(0);

    // Trim options
    const [trimStart, setTrimStart] = useState(0);
    const [trimEnd, setTrimEnd] = useState(10);
    const [trimAccurate, setTrimAccurate] = useState(true);

    // Compress options
    const [compPreset, setCompPreset] = useState("balanced");
    const [compCrf, setCompCrf] = useState(26);
    const [compMaxH, setCompMaxH] = useState(1080);

    // Convert options
    const [targetFormat, setTargetFormat] = useState("mp4");

    // Transform options
    const [rotate, setRotate] = useState(90);
    const [flipH, setFlipH] = useState(false);
    const [flipV, setFlipV] = useState(false);
    const [aspect, setAspect] = useState("keep");

    const [running, setRunning] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<QuickToolResult | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handlePickFile = (f: File) => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        setFile(f);
        setPreviewUrl(URL.createObjectURL(f));
        setResult(null);
        setError(null);
    };

    const handleRun = async () => {
        if (!file || running) return;
        setRunning(true);
        setError(null);
        setResult(null);
        try {
            let res: QuickToolResult;
            if (activeTool === "trim") {
                res = await runQuickTrim(file, trimStart, trimEnd, trimAccurate);
            } else if (activeTool === "compress") {
                res = await runQuickCompress(file, compPreset, compCrf, compMaxH);
            } else if (activeTool === "convert") {
                res = await runQuickConvert(file, targetFormat);
            } else {
                res = await runQuickTransform(file, rotate, flipH, flipV, aspect);
            }
            setResult(res);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Processing failed");
        } finally {
            setRunning(false);
        }
    };

    const tabs: { id: ToolTab; label: string; desc: string; icon: React.ReactNode }[] = [
        { id: "trim", label: "Trim", desc: "Cut start/end points", icon: <Scissors className="h-4 w-4" /> },
        { id: "compress", label: "Compress", desc: "Shrink file size", icon: <Minimize2 className="h-4 w-4" /> },
        { id: "convert", label: "Convert", desc: "MP4/MOV/GIF/MP3", icon: <RefreshCw className="h-4 w-4" /> },
        { id: "transform", label: "Rotate & Resize", desc: "Rotate, flip, 9:16", icon: <RotateCw className="h-4 w-4" /> },
    ];

    const baseUrl = getApiBaseUrl();
    const outUrl = result ? `${baseUrl}${result.download_url}` : "";

    return (
        <div className="mx-auto w-full max-w-5xl px-6 py-10 space-y-8">
            <div>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/20 border border-primary/30 px-3.5 py-1 text-xs font-bold uppercase tracking-widest text-indigo-300 mb-3">
                    <Wrench className="h-3.5 w-3.5" />
                    Local Quick-Tools
                </span>
                <h1 className="text-3xl font-bold text-white">
                    No-watermark video utilities, <span className="text-gradient">100% local</span>
                </h1>
                <p className="text-sm text-slate-400 mt-1.5">
                    Trim, compress, convert, or rotate any video directly on your machine via FFmpeg with zero cloud uploads.
                </p>
            </div>

            {/* Tool Selector */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {tabs.map((t) => (
                    <button
                        key={t.id}
                        type="button"
                        onClick={() => {
                            setActiveTool(t.id);
                            setResult(null);
                            setError(null);
                        }}
                        className={`rounded-2xl p-4 text-left border transition-all ${
                            activeTool === t.id
                                ? "border-primary bg-primary/15 text-white shadow-lg"
                                : "border-white/10 bg-white/[0.02] text-slate-400 hover:border-white/25 hover:text-white"
                        }`}
                    >
                        <div className="flex items-center gap-2 font-bold text-sm">
                            {t.icon}
                            {t.label}
                        </div>
                        <p className="text-xs opacity-70 mt-1">{t.desc}</p>
                    </button>
                ))}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Left: Input Upload + Preview */}
                <div className="glass-card rounded-3xl p-6 space-y-4">
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="video/*"
                        className="hidden"
                        onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (f) handlePickFile(f);
                        }}
                    />

                    {!file ? (
                        <div
                            onClick={() => fileInputRef.current?.click()}
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={(e) => {
                                e.preventDefault();
                                const f = e.dataTransfer.files?.[0];
                                if (f) handlePickFile(f);
                            }}
                            className="h-72 rounded-2xl border-2 border-dashed border-white/15 hover:border-primary/60 bg-white/[0.02] hover:bg-primary/5 flex flex-col items-center justify-center gap-2 cursor-pointer transition-all"
                        >
                            <Upload className="h-8 w-8 text-primary" />
                            <p className="text-sm font-bold text-white">Drop a video file or click to browse</p>
                            <p className="text-xs text-slate-500">MP4, MOV, MKV, WEBM, AVI</p>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            <div className="aspect-video rounded-2xl overflow-hidden bg-black border border-white/10">
                                {previewUrl && (
                                    <video
                                        src={previewUrl}
                                        controls
                                        onLoadedMetadata={(e) => {
                                            const d = (e.target as HTMLVideoElement).duration;
                                            if (d && Number.isFinite(d)) {
                                                setFileDuration(d);
                                                setTrimStart(0);
                                                setTrimEnd(Number(d.toFixed(1)));
                                            }
                                        }}
                                        className="w-full h-full object-contain"
                                    />
                                )}
                            </div>
                            <div className="flex items-center justify-between text-xs text-slate-400">
                                <span className="truncate font-semibold text-white">{file.name}</span>
                                <div className="flex items-center gap-3 flex-shrink-0">
                                    <span>{formatBytes(file.size)}</span>
                                    <button
                                        type="button"
                                        onClick={() => fileInputRef.current?.click()}
                                        className="text-indigo-300 font-bold hover:underline"
                                    >
                                        Change
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Right: Tool Settings + Run */}
                <div className="glass-card rounded-3xl p-6 flex flex-col justify-between gap-6">
                    <div className="space-y-4">
                        <h2 className="text-base font-bold text-white capitalize">
                            {activeTool === "transform" ? "Rotate, Flip & Resize" : `${activeTool} Settings`}
                        </h2>

                        {activeTool === "trim" && (
                            <div className="space-y-4">
                                <div className="grid grid-cols-2 gap-3">
                                    <label className="text-xs text-slate-400">
                                        Start (seconds)
                                        <input
                                            type="number"
                                            step={0.1}
                                            min={0}
                                            max={Math.max(0, trimEnd - 0.1)}
                                            value={trimStart}
                                            onChange={(e) => setTrimStart(Number(e.target.value))}
                                            className="mt-1 w-full rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2 text-sm font-mono text-white"
                                        />
                                    </label>
                                    <label className="text-xs text-slate-400">
                                        End (seconds){fileDuration ? ` / ${fileDuration.toFixed(1)}s` : ""}
                                        <input
                                            type="number"
                                            step={0.1}
                                            min={trimStart + 0.1}
                                            value={trimEnd}
                                            onChange={(e) => setTrimEnd(Number(e.target.value))}
                                            className="mt-1 w-full rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2 text-sm font-mono text-white"
                                        />
                                    </label>
                                </div>
                                <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={trimAccurate}
                                        onChange={(e) => setTrimAccurate(e.target.checked)}
                                        className="accent-primary"
                                    />
                                    Frame-accurate re-encode (uncheck for instant keyframe stream-copy)
                                </label>
                            </div>
                        )}

                        {activeTool === "compress" && (
                            <div className="space-y-4">
                                <div className="grid grid-cols-2 gap-2">
                                    {[
                                        { id: "high", label: "High Quality", crf: 22, h: 1080 },
                                        { id: "balanced", label: "Balanced (Web)", crf: 26, h: 1080 },
                                        { id: "small", label: "Small (720p)", crf: 30, h: 720 },
                                        { id: "discord", label: "Ultra Small", crf: 32, h: 720 },
                                    ].map((p) => (
                                        <button
                                            key={p.id}
                                            type="button"
                                            onClick={() => {
                                                setCompPreset(p.id);
                                                setCompCrf(p.crf);
                                                setCompMaxH(p.h);
                                            }}
                                            className={`rounded-xl p-2.5 border text-left text-xs font-bold ${
                                                compPreset === p.id
                                                    ? "border-primary bg-primary/15 text-white"
                                                    : "border-white/10 bg-white/[0.02] text-slate-400"
                                            }`}
                                        >
                                            {p.label}
                                        </button>
                                    ))}
                                </div>
                                <div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span className="text-slate-400">Compression (CRF)</span>
                                        <span className="font-mono text-white">{compCrf} (higher = smaller)</span>
                                    </div>
                                    <input
                                        type="range"
                                        min={18}
                                        max={36}
                                        value={compCrf}
                                        onChange={(e) => setCompCrf(Number(e.target.value))}
                                        className="w-full accent-primary"
                                    />
                                </div>
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1">Max Height</label>
                                    <select
                                        value={compMaxH}
                                        onChange={(e) => setCompMaxH(Number(e.target.value))}
                                        className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2 text-sm text-white"
                                    >
                                        <option value={2160} className="bg-slate-900">Keep Original (up to 4K)</option>
                                        <option value={1080} className="bg-slate-900">1080p HD</option>
                                        <option value={720} className="bg-slate-900">720p</option>
                                        <option value={480} className="bg-slate-900">480p</option>
                                    </select>
                                </div>
                            </div>
                        )}

                        {activeTool === "convert" && (
                            <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                                {["mp4", "mov", "webm", "mkv", "gif", "mp3", "wav"].map((fmt) => (
                                    <button
                                        key={fmt}
                                        type="button"
                                        onClick={() => setTargetFormat(fmt)}
                                        className={`py-3 rounded-xl border text-xs font-black uppercase tracking-wider transition-all ${
                                            targetFormat === fmt
                                                ? "border-primary bg-primary/20 text-white"
                                                : "border-white/10 bg-white/[0.02] text-slate-400 hover:border-white/25"
                                        }`}
                                    >
                                        .{fmt}
                                    </button>
                                ))}
                            </div>
                        )}

                        {activeTool === "transform" && (
                            <div className="space-y-4">
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1.5">Rotation</label>
                                    <div className="grid grid-cols-4 gap-2">
                                        {[0, 90, 180, 270].map((deg) => (
                                            <button
                                                key={deg}
                                                type="button"
                                                onClick={() => setRotate(deg)}
                                                className={`py-2 rounded-xl border text-xs font-bold ${
                                                    rotate === deg
                                                        ? "border-primary bg-primary/20 text-white"
                                                        : "border-white/10 bg-white/[0.02] text-slate-400"
                                                }`}
                                            >
                                                {deg}°
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <div className="flex gap-4">
                                    <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                                        <input type="checkbox" checked={flipH} onChange={(e) => setFlipH(e.target.checked)} className="accent-primary" />
                                        Flip Horizontal
                                    </label>
                                    <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                                        <input type="checkbox" checked={flipV} onChange={(e) => setFlipV(e.target.checked)} className="accent-primary" />
                                        Flip Vertical
                                    </label>
                                </div>
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1.5">Aspect Ratio Canvas</label>
                                    <div className="grid grid-cols-4 gap-2">
                                        {["keep", "9:16", "16:9", "1:1"].map((ar) => (
                                            <button
                                                key={ar}
                                                type="button"
                                                onClick={() => setAspect(ar)}
                                                className={`py-2 rounded-xl border text-xs font-bold capitalize ${
                                                    aspect === ar
                                                        ? "border-primary bg-primary/20 text-white"
                                                        : "border-white/10 bg-white/[0.02] text-slate-400"
                                                }`}
                                            >
                                                {ar}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="space-y-3">
                        {error && (
                            <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-2.5 text-xs text-red-400">
                                {error}
                            </div>
                        )}
                        <button
                            type="button"
                            disabled={!file || running}
                            onClick={handleRun}
                            className="w-full py-3.5 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-50 text-sm font-bold text-white flex items-center justify-center gap-2 glow-primary transition-all"
                        >
                            {running ? (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Processing Locally…
                                </>
                            ) : (
                                <>Run {activeTool === "transform" ? "Transform" : activeTool}</>
                            )}
                        </button>
                    </div>
                </div>
            </div>

            {/* Result Card */}
            {result && (
                <div className="glass-card rounded-3xl p-6 border border-emerald-500/30 space-y-4">
                    <div className="flex items-center justify-between gap-4 flex-wrap">
                        <div className="flex items-center gap-2 text-emerald-300 font-bold text-sm">
                            <CheckCircle2 className="h-5 w-5" />
                            Ready: {result.filename}
                        </div>
                        <a
                            href={outUrl}
                            download={result.filename}
                            className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-xs font-bold text-white flex items-center gap-1.5"
                        >
                            <Download className="h-4 w-4" />
                            Download Output
                        </a>
                    </div>

                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                        <div className="rounded-xl bg-white/5 p-3">
                            <p className="text-slate-500">Original Size</p>
                            <p className="font-bold text-white mt-0.5">{formatBytes(result.input.size_bytes)}</p>
                        </div>
                        <div className="rounded-xl bg-white/5 p-3">
                            <p className="text-slate-500">Output Size</p>
                            <p className="font-bold text-emerald-300 mt-0.5">
                                {formatBytes(result.output.size_bytes)}
                                {result.savings_percent !== undefined ? ` (${result.savings_percent}% saved)` : ""}
                            </p>
                        </div>
                        <div className="rounded-xl bg-white/5 p-3">
                            <p className="text-slate-500">Duration</p>
                            <p className="font-bold text-white mt-0.5">{result.output.duration.toFixed(2)}s</p>
                        </div>
                        <div className="rounded-xl bg-white/5 p-3">
                            <p className="text-slate-500">Dimensions</p>
                            <p className="font-bold text-white mt-0.5">
                                {result.output.width ? `${result.output.width}×${result.output.height}` : "Audio Only"}
                            </p>
                        </div>
                    </div>

                    {result.format === "mp3" || result.format === "wav" ? (
                        <audio src={outUrl} controls className="w-full" />
                    ) : result.format === "gif" ? (
                        <img src={outUrl} alt="Converted GIF" className="max-h-72 rounded-xl mx-auto" />
                    ) : (
                        <video src={outUrl} controls className="w-full max-h-80 rounded-xl bg-black" />
                    )}
                </div>
            )}
        </div>
    );
}
