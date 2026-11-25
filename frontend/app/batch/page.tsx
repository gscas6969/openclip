"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
    cancelBatchItem,
    createBatchJob,
    getBatches,
    getBrandKits,
} from "@/lib/api";
import { BatchJob, BrandKit } from "@/lib/types";
import { Layers, Play, XCircle, CheckCircle2, AlertCircle, Clock, Loader2 } from "lucide-react";

const DURATION_PRESETS = [
    { label: "Short (15–30s)", min: 15, max: 30 },
    { label: "Standard (30–60s)", min: 30, max: 60 },
    { label: "Deep Dive (60–90s)", min: 60, max: 90 },
] as const;

const LAYOUT_PRESETS = [
    { value: "auto", label: "Auto Detect" },
    { value: "dynamic_zoom", label: "Smart Face Track" },
    { value: "podcast_split", label: "Podcast Split" },
    { value: "podcast_switch", label: "Podcast Speaker Switch" },
    { value: "tutorial", label: "Tutorial (Screen + Face)" },
    { value: "original", label: "Original + Blur Fill" },
] as const;

function StatusPill({ status }: { status: string }) {
    const map: Record<string, { cls: string; icon: React.ReactNode }> = {
        done: { cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30", icon: <CheckCircle2 className="h-3 w-3" /> },
        error: { cls: "bg-red-500/15 text-red-300 border-red-500/30", icon: <AlertCircle className="h-3 w-3" /> },
        cancelled: { cls: "bg-slate-500/15 text-slate-400 border-slate-500/30", icon: <XCircle className="h-3 w-3" /> },
        queued: { cls: "bg-amber-500/15 text-amber-300 border-amber-500/30", icon: <Clock className="h-3 w-3" /> },
    };
    const style = map[status] || {
        cls: "bg-primary/20 text-indigo-300 border-primary/40",
        icon: <Loader2 className="h-3 w-3 animate-spin" />,
    };
    return (
        <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${style.cls}`}>
            {style.icon}
            {status}
        </span>
    );
}

export default function BatchPage() {
    const [urlsText, setUrlsText] = useState("");
    const [minDur, setMinDur] = useState(30);
    const [maxDur, setMaxDur] = useState(60);
    const [layoutPref, setLayoutPref] = useState("auto");
    const [brandKits, setBrandKits] = useState<BrandKit[]>([]);
    const [brandKitId, setBrandKitId] = useState("");
    const [customPrompt, setCustomPrompt] = useState("");

    const [batches, setBatches] = useState<BatchJob[]>([]);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const refreshBatches = () => {
        getBatches().then(setBatches).catch(console.error);
    };

    useEffect(() => {
        getBrandKits().then(setBrandKits).catch(console.error);
        refreshBatches();
    }, []);

    // Poll while any batch is running
    useEffect(() => {
        const hasRunning = batches.some((b) => b.status === "running");
        if (!hasRunning) return;
        const t = setInterval(refreshBatches, 3000);
        return () => clearInterval(t);
    }, [batches]);

    const parsedUrls = urlsText
        .split(/\r?\n/)
        .map((u) => u.trim())
        .filter(Boolean);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (parsedUrls.length === 0 || submitting) return;
        setSubmitting(true);
        setError(null);
        try {
            const created = await createBatchJob(parsedUrls, {
                clip_min_duration: minDur,
                clip_max_duration: maxDur,
                layout_preference: layoutPref,
                brand_kit_id: brandKitId || null,
                custom_prompt: customPrompt.trim() || undefined,
            });
            setBatches((prev) => [created, ...prev]);
            setUrlsText("");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to submit batch");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="mx-auto w-full max-w-5xl px-6 py-10 space-y-10">
            <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/20 border border-primary/30 px-3.5 py-1 text-xs font-bold uppercase tracking-widest text-indigo-300 mb-3">
                        <Layers className="h-3.5 w-3.5" />
                        Batch Queue
                    </span>
                    <h1 className="text-3xl font-bold text-white">
                        Process multiple videos <span className="text-gradient">automatically</span>
                    </h1>
                    <p className="text-sm text-slate-400 mt-1.5">
                        Paste up to 25 YouTube URLs. OpenClip processes them sequentially in the background so your machine stays responsive.
                    </p>
                </div>
            </div>

            {/* Submission Form */}
            <form onSubmit={handleSubmit} className="glass-card rounded-3xl p-7 space-y-5">
                <div>
                    <div className="flex items-center justify-between mb-2">
                        <label className="text-xs font-bold uppercase tracking-wider text-slate-400">
                            YouTube URLs (one per line)
                        </label>
                        <span className="text-xs font-mono text-indigo-300">
                            {parsedUrls.length} / 25 URLs
                        </span>
                    </div>
                    <textarea
                        rows={5}
                        value={urlsText}
                        onChange={(e) => setUrlsText(e.target.value)}
                        placeholder={"https://www.youtube.com/watch?v=...\nhttps://youtu.be/...\nhttps://www.youtube.com/watch?v=..."}
                        className="w-full rounded-2xl bg-slate-950/70 border border-white/10 p-4 text-sm font-mono text-white placeholder-slate-600 focus:border-primary focus:outline-none"
                    />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                            Clip Length
                        </label>
                        <select
                            value={`${minDur}-${maxDur}`}
                            onChange={(e) => {
                                const [mn, mx] = e.target.value.split("-").map(Number);
                                setMinDur(mn);
                                setMaxDur(mx);
                            }}
                            className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2.5 text-sm text-white focus:border-primary focus:outline-none"
                        >
                            {DURATION_PRESETS.map((p) => (
                                <option key={p.label} value={`${p.min}-${p.max}`} className="bg-slate-900">
                                    {p.label}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                            Reframe Layout
                        </label>
                        <select
                            value={layoutPref}
                            onChange={(e) => setLayoutPref(e.target.value)}
                            className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2.5 text-sm text-white focus:border-primary focus:outline-none"
                        >
                            {LAYOUT_PRESETS.map((l) => (
                                <option key={l.value} value={l.value} className="bg-slate-900">
                                    {l.label}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                            Brand Kit
                        </label>
                        <select
                            value={brandKitId}
                            onChange={(e) => setBrandKitId(e.target.value)}
                            className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2.5 text-sm text-white focus:border-primary focus:outline-none"
                        >
                            <option value="" className="bg-slate-900">Default Caption Style</option>
                            {brandKits.map((k) => (
                                <option key={k.id} value={k.id} className="bg-slate-900">
                                    {k.name}
                                </option>
                            ))}
                        </select>
                    </div>
                </div>

                <div>
                    <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                        Shared AI Focus Prompt (Optional)
                    </label>
                    <input
                        value={customPrompt}
                        onChange={(e) => setCustomPrompt(e.target.value)}
                        placeholder="e.g. Extract actionable growth advice and punchy hooks..."
                        className="w-full rounded-xl bg-slate-900/60 border border-white/10 px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:border-primary focus:outline-none"
                    />
                </div>

                {error && (
                    <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400">
                        {error}
                    </div>
                )}

                <button
                    type="submit"
                    disabled={parsedUrls.length === 0 || submitting}
                    className="w-full rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-50 py-3.5 text-sm font-bold text-white flex items-center justify-center gap-2 glow-primary transition-all"
                >
                    {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                    Queue {parsedUrls.length || 0} Video{parsedUrls.length === 1 ? "" : "s"} for Clipping
                </button>
            </form>

            {/* Active & Past Batches */}
            <section className="space-y-4">
                <div className="flex items-center justify-between">
                    <h2 className="text-lg font-bold text-white">Batch Runs ({batches.length})</h2>
                    <button
                        type="button"
                        onClick={refreshBatches}
                        className="text-xs font-semibold text-indigo-300 hover:underline"
                    >
                        Refresh
                    </button>
                </div>

                {batches.length === 0 ? (
                    <div className="glass rounded-2xl p-10 text-center text-slate-500 text-sm">
                        No batch jobs yet. Paste a list of YouTube URLs above to queue your first batch.
                    </div>
                ) : (
                    batches.map((batch) => {
                        const pct = batch.total > 0 ? Math.round(((batch.completed + batch.failed) / batch.total) * 100) : 0;
                        return (
                            <div key={batch.id} className="glass-card rounded-2xl p-6 space-y-4 border border-white/10">
                                <div className="flex items-center justify-between gap-4 flex-wrap">
                                    <div className="flex items-center gap-3">
                                        <span className="font-mono text-xs text-slate-400">{batch.id}</span>
                                        <StatusPill status={batch.status} />
                                    </div>
                                    <div className="text-xs text-slate-400">
                                        <strong className="text-white">{batch.completed}</strong>/{batch.total} done
                                        {batch.queued > 0 ? ` · ${batch.queued} queued` : ""}
                                        {batch.failed > 0 ? ` · ${batch.failed} failed` : ""}
                                    </div>
                                </div>

                                <div className="h-2 w-full rounded-full bg-white/5 overflow-hidden">
                                    <div
                                        className="h-full rounded-full transition-all duration-500"
                                        style={{
                                            width: `${pct}%`,
                                            background: "linear-gradient(to right, #2e1ded, #8b5cf6)",
                                        }}
                                    />
                                </div>

                                <div className="space-y-2">
                                    {batch.items.map((item) => (
                                        <div
                                            key={item.project_id}
                                            className="rounded-xl bg-white/[0.03] border border-white/5 px-4 py-3 flex items-center justify-between gap-3"
                                        >
                                            <div className="min-w-0 flex-1">
                                                <p className="text-sm font-semibold text-white truncate">
                                                    {item.title || item.youtube_url}
                                                </p>
                                                <p className="text-xs text-slate-500 truncate">
                                                    {item.youtube_url}
                                                    {item.clip_count > 0 ? ` · ${item.clip_count} clips` : ""}
                                                    {item.error_message ? ` · ${item.error_message}` : ""}
                                                </p>
                                            </div>
                                            <div className="flex items-center gap-2 flex-shrink-0">
                                                <StatusPill status={item.status} />
                                                {item.status === "queued" && (
                                                    <button
                                                        type="button"
                                                        onClick={async () => {
                                                            await cancelBatchItem(batch.id, item.project_id);
                                                            refreshBatches();
                                                        }}
                                                        className="px-2.5 py-1 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-[11px] font-bold text-red-300"
                                                    >
                                                        Cancel
                                                    </button>
                                                )}
                                                <Link
                                                    href={`/project/${item.project_id}`}
                                                    className="px-3 py-1 rounded-lg bg-white/5 hover:bg-white/10 text-[11px] font-bold text-slate-200 border border-white/10"
                                                >
                                                    Open →
                                                </Link>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        );
                    })
                )}
            </section>
        </div>
    );
}
