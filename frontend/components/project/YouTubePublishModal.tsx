"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
    getYouTubeStatus,
    publishClipToYouTube,
    scheduleProjectToYouTube,
} from "@/lib/api";
import { Clip, YouTubePublishResult, YouTubeStatus } from "@/lib/types";
import {
    X,
    Youtube,
    Calendar,
    Loader2,
    CheckCircle2,
    ExternalLink,
    AlertTriangle,
} from "lucide-react";

function defaultScheduleLocal(): string {
    const d = new Date(Date.now() + 2 * 3600 * 1000);
    d.setMinutes(0, 0, 0);
    const pad = (n: number) => n.toString().padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:00`;
}

interface SingleProps {
    mode: "single";
    clip: Clip;
    onClose: () => void;
    onPublished?: (res: YouTubePublishResult) => void;
}

interface BatchProps {
    mode: "batch";
    projectId: string;
    clipCount: number;
    unpostedCount: number;
    onClose: () => void;
    onScheduled?: () => void;
}

export type YouTubePublishModalProps = SingleProps | BatchProps;

export function YouTubePublishModal(props: YouTubePublishModalProps) {
    const [ytStatus, setYtStatus] = useState<YouTubeStatus | null>(null);
    const [checking, setChecking] = useState(true);

    // Single clip state
    const initialTitle = props.mode === "single" ? (props.clip.title || "OpenClip Short").slice(0, 100) : "";
    const initialDesc =
        props.mode === "single"
            ? [
                  props.clip.reason || "",
                  (props.clip.hashtags || []).join(" "),
                  "#Shorts",
              ]
                  .filter(Boolean)
                  .join("\n\n")
            : "";
    const initialTags = props.mode === "single" ? (props.clip.tags || []).join(", ") : "";

    const [title, setTitle] = useState(initialTitle);
    const [description, setDescription] = useState(initialDesc);
    const [tagsText, setTagsText] = useState(initialTags);
    const [publishMode, setPublishMode] = useState<"private" | "unlisted" | "public" | "schedule">("private");
    const [scheduleLocal, setScheduleLocal] = useState(defaultScheduleLocal);
    const [madeForKids, setMadeForKids] = useState(false);

    // Batch schedule state
    const [intervalHours, setIntervalHours] = useState(24);
    const [descFooter, setDescFooter] = useState("");
    const [maxUploads, setMaxUploads] = useState(6);

    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [singleResult, setSingleResult] = useState<YouTubePublishResult | null>(null);
    const [batchResult, setBatchResult] = useState<{
        scheduled: (YouTubePublishResult & { clip_id: string; title?: string })[];
        errors: { clip_id: string; title?: string; error: string }[];
    } | null>(null);

    useEffect(() => {
        getYouTubeStatus()
            .then(setYtStatus)
            .finally(() => setChecking(false));
    }, []);

    const handleSinglePublish = async (e: React.FormEvent) => {
        e.preventDefault();
        if (props.mode !== "single" || submitting) return;
        setSubmitting(true);
        setError(null);
        try {
            const publishAtIso =
                publishMode === "schedule"
                    ? new Date(scheduleLocal).toISOString().replace(/\.\d{3}Z$/, "Z")
                    : null;
            const tags = tagsText
                .split(",")
                .map((t) => t.trim())
                .filter(Boolean);

            const res = await publishClipToYouTube(props.clip.id, {
                title: title.trim(),
                description,
                tags,
                privacy_status: publishMode === "schedule" ? "private" : publishMode,
                publish_at: publishAtIso,
                made_for_kids: madeForKids,
            });
            setSingleResult(res);
            props.onPublished?.(res);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Upload failed");
        } finally {
            setSubmitting(false);
        }
    };

    const handleBatchSchedule = async (e: React.FormEvent) => {
        e.preventDefault();
        if (props.mode !== "batch" || submitting) return;
        setSubmitting(true);
        setError(null);
        try {
            const startIso = new Date(scheduleLocal).toISOString().replace(/\.\d{3}Z$/, "Z");
            const res = await scheduleProjectToYouTube(props.projectId, {
                start_at: startIso,
                interval_hours: intervalHours,
                description_footer: descFooter,
                skip_already_posted: true,
                max_uploads: maxUploads,
            });
            setBatchResult(res);
            props.onScheduled?.();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Auto-schedule failed");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-md flex items-center justify-center p-4">
            <div className="w-full max-w-lg rounded-3xl bg-[#0b0b12] border border-white/15 shadow-2xl overflow-hidden">
                {/* Header */}
                <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                        <div className="h-8 w-8 rounded-xl bg-red-600/20 border border-red-500/30 flex items-center justify-center text-red-400">
                            <Youtube className="h-4 w-4" />
                        </div>
                        <div>
                            <h3 className="text-sm font-bold text-white">
                                {props.mode === "single"
                                    ? "Post Short to YouTube"
                                    : "Auto-Schedule All Clips to YouTube"}
                            </h3>
                            {ytStatus?.connected && ytStatus.channel_title && (
                                <p className="text-[11px] text-emerald-400">
                                    Connected: {ytStatus.channel_title}
                                </p>
                            )}
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={props.onClose}
                        className="h-8 w-8 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-slate-400 hover:text-white"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                {/* Body */}
                <div className="p-6 max-h-[80vh] overflow-y-auto custom-scrollbar">
                    {checking ? (
                        <div className="py-12 flex flex-col items-center gap-3 text-slate-400 text-xs">
                            <Loader2 className="h-6 w-6 animate-spin text-primary" />
                            Checking YouTube connection…
                        </div>
                    ) : !ytStatus?.connected ? (
                        <div className="space-y-4 text-center py-4">
                            <div className="h-12 w-12 rounded-2xl bg-red-500/15 border border-red-500/25 flex items-center justify-center text-red-400 mx-auto">
                                <Youtube className="h-6 w-6" />
                            </div>
                            <div>
                                <p className="text-sm font-bold text-white">Connect Your YouTube Channel First</p>
                                <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
                                    Upload your free Google Cloud OAuth <code className="text-slate-200">client_secret.json</code> in Settings and sign in once to enable one-click YouTube Shorts posting &amp; scheduling.
                                </p>
                            </div>
                            <Link
                                href="/settings#youtube"
                                onClick={props.onClose}
                                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-xs font-bold text-white transition-colors"
                            >
                                Set Up YouTube in Settings →
                            </Link>
                        </div>
                    ) : singleResult ? (
                        <div className="space-y-4 text-center py-4">
                            <CheckCircle2 className="h-12 w-12 text-emerald-400 mx-auto" />
                            <div>
                                <p className="text-base font-bold text-white">
                                    {singleResult.status === "scheduled"
                                        ? "Short Scheduled on YouTube!"
                                        : "Short Uploaded to YouTube!"}
                                </p>
                                <p className="text-xs text-slate-400 mt-1">
                                    Status: <strong className="text-white uppercase">{singleResult.status}</strong>
                                    {singleResult.publish_at ? ` · Publishes ${new Date(singleResult.publish_at).toLocaleString()}` : ""}
                                </p>
                            </div>
                            <div className="flex gap-2 justify-center pt-2">
                                <a
                                    href={singleResult.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="px-4 py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-xs font-bold text-white flex items-center gap-1.5"
                                >
                                    <ExternalLink className="h-3.5 w-3.5" />
                                    View Short
                                </a>
                                <a
                                    href={singleResult.studio_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="px-4 py-2.5 rounded-xl bg-white/10 hover:bg-white/15 text-xs font-bold text-white flex items-center gap-1.5"
                                >
                                    Open YouTube Studio
                                </a>
                            </div>
                        </div>
                    ) : batchResult ? (
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-emerald-300 font-bold text-sm">
                                <CheckCircle2 className="h-5 w-5" />
                                Scheduled {batchResult.scheduled.length} Short{batchResult.scheduled.length === 1 ? "" : "s"}!
                            </div>
                            <div className="space-y-2">
                                {batchResult.scheduled.map((item) => (
                                    <div key={item.video_id} className="rounded-xl bg-white/5 border border-white/10 p-3 flex items-center justify-between gap-2 text-xs">
                                        <div className="min-w-0">
                                            <p className="font-bold text-white truncate">{item.title || item.video_id}</p>
                                            <p className="text-slate-400 text-[11px]">
                                                {item.publish_at ? new Date(item.publish_at).toLocaleString() : item.status}
                                            </p>
                                        </div>
                                        <a href={item.studio_url} target="_blank" rel="noreferrer" className="text-indigo-300 font-bold hover:underline flex-shrink-0">
                                            Studio →
                                        </a>
                                    </div>
                                ))}
                                {batchResult.errors.map((er, i) => (
                                    <div key={i} className="rounded-xl bg-red-500/10 border border-red-500/20 p-3 text-xs text-red-300">
                                        {er.title || er.clip_id}: {er.error}
                                    </div>
                                ))}
                            </div>
                            <button type="button" onClick={props.onClose} className="w-full py-2.5 rounded-xl bg-white/10 hover:bg-white/15 text-xs font-bold text-white">
                                Done
                            </button>
                        </div>
                    ) : props.mode === "single" ? (
                        <form onSubmit={handleSinglePublish} className="space-y-4">
                            <div>
                                <div className="flex justify-between text-[11px] mb-1">
                                    <label className="font-bold uppercase tracking-wider text-slate-400">Title</label>
                                    <span className="font-mono text-slate-500">{title.length}/100</span>
                                </div>
                                <input
                                    value={title}
                                    maxLength={100}
                                    required
                                    onChange={(e) => setTitle(e.target.value)}
                                    className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3.5 py-2.5 text-sm text-white focus:border-primary focus:outline-none"
                                />
                            </div>

                            <div>
                                <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                                    Description (#Shorts added automatically)
                                </label>
                                <textarea
                                    rows={4}
                                    value={description}
                                    onChange={(e) => setDescription(e.target.value)}
                                    className="w-full rounded-xl bg-slate-900/70 border border-white/10 p-3 text-xs text-white focus:border-primary focus:outline-none"
                                />
                            </div>

                            <div>
                                <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                                    Tags (comma separated)
                                </label>
                                <input
                                    value={tagsText}
                                    onChange={(e) => setTagsText(e.target.value)}
                                    placeholder="shorts, podcast, viral"
                                    className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3.5 py-2 text-xs text-white focus:border-primary focus:outline-none"
                                />
                            </div>

                            <div>
                                <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1.5">
                                    Visibility / Schedule
                                </label>
                                <div className="grid grid-cols-4 gap-1.5">
                                    {(["private", "unlisted", "public", "schedule"] as const).map((m) => (
                                        <button
                                            key={m}
                                            type="button"
                                            onClick={() => setPublishMode(m)}
                                            className={`py-2 rounded-xl text-xs font-bold capitalize border transition-all ${
                                                publishMode === m
                                                    ? "border-red-500 bg-red-600/20 text-white"
                                                    : "border-white/10 bg-white/[0.02] text-slate-400 hover:text-white"
                                            }`}
                                        >
                                            {m}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {publishMode === "schedule" && (
                                <div>
                                    <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                                        Publish Date &amp; Time (Local)
                                    </label>
                                    <input
                                        type="datetime-local"
                                        required
                                        value={scheduleLocal}
                                        onChange={(e) => setScheduleLocal(e.target.value)}
                                        className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3.5 py-2.5 text-sm text-white focus:border-primary focus:outline-none"
                                    />
                                </div>
                            )}

                            <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={madeForKids}
                                    onChange={(e) => setMadeForKids(e.target.checked)}
                                    className="accent-red-500"
                                />
                                Made for Kids
                            </label>

                            {error && (
                                <div className="rounded-xl bg-red-500/10 border border-red-500/25 p-3 text-xs text-red-300 flex items-start gap-2">
                                    <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5" />
                                    <span>{error}</span>
                                </div>
                            )}

                            <button
                                type="submit"
                                disabled={submitting}
                                className="w-full py-3 rounded-xl bg-red-600 hover:bg-red-500 disabled:opacity-50 text-sm font-bold text-white flex items-center justify-center gap-2 transition-colors"
                            >
                                {submitting ? (
                                    <>
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        Uploading to YouTube…
                                    </>
                                ) : (
                                    <>
                                        <Youtube className="h-4 w-4" />
                                        {publishMode === "schedule" ? "Upload & Schedule Short" : `Upload as ${publishMode}`}
                                    </>
                                )}
                            </button>
                        </form>
                    ) : (
                        <form onSubmit={handleBatchSchedule} className="space-y-4">
                            <p className="text-xs text-slate-400">
                                Automatically uploads &amp; schedules up to <strong>{Math.min(maxUploads, props.unpostedCount)}</strong> unposted clip(s) (of {props.clipCount} total) using YouTube&apos;s native scheduler.
                            </p>

                            <div>
                                <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                                    First Short Publishes At (Local)
                                </label>
                                <input
                                    type="datetime-local"
                                    required
                                    value={scheduleLocal}
                                    onChange={(e) => setScheduleLocal(e.target.value)}
                                    className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3.5 py-2.5 text-sm text-white"
                                />
                            </div>

                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                                        Cadence
                                    </label>
                                    <select
                                        value={intervalHours}
                                        onChange={(e) => setIntervalHours(Number(e.target.value))}
                                        className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2.5 text-xs text-white"
                                    >
                                        <option value={6} className="bg-slate-900">Every 6 hours</option>
                                        <option value={12} className="bg-slate-900">Every 12 hours (2/day)</option>
                                        <option value={24} className="bg-slate-900">Every 24 hours (1/day)</option>
                                        <option value={48} className="bg-slate-900">Every 2 days</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                                        Max Clips (Free Quota: 6/day)
                                    </label>
                                    <input
                                        type="number"
                                        min={1}
                                        max={10}
                                        value={maxUploads}
                                        onChange={(e) => setMaxUploads(Number(e.target.value))}
                                        className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2 text-sm text-white"
                                    />
                                </div>
                            </div>

                            <div>
                                <label className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                                    Description Footer (Optional)
                                </label>
                                <input
                                    value={descFooter}
                                    onChange={(e) => setDescFooter(e.target.value)}
                                    placeholder="e.g. Subscribe for daily clips!"
                                    className="w-full rounded-xl bg-slate-900/70 border border-white/10 px-3.5 py-2 text-xs text-white"
                                />
                            </div>

                            {error && (
                                <div className="rounded-xl bg-red-500/10 border border-red-500/25 p-3 text-xs text-red-300">
                                    {error}
                                </div>
                            )}

                            <button
                                type="submit"
                                disabled={submitting || props.unpostedCount === 0}
                                className="w-full py-3 rounded-xl bg-red-600 hover:bg-red-500 disabled:opacity-50 text-sm font-bold text-white flex items-center justify-center gap-2"
                            >
                                {submitting ? (
                                    <>
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        Uploading &amp; Scheduling Clips…
                                    </>
                                ) : (
                                    <>
                                        <Calendar className="h-4 w-4" />
                                        Auto-Schedule {Math.min(maxUploads, props.unpostedCount)} Clip(s)
                                    </>
                                )}
                            </button>
                        </form>
                    )}
                </div>
            </div>
        </div>
    );
}
