"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
    getProject,
    getApiBaseUrl,
    getExportAllUrl,
    retryProject,
    relayoutProject,
} from "@/lib/api";
import { Project, Clip } from "@/lib/types";
import { YouTubePublishModal } from "@/components/project/YouTubePublishModal";
import { Sliders, Download, Archive, Play, Youtube, Calendar, CheckCircle2, LayoutTemplate, Loader2 } from "lucide-react";

function formatDuration(s: number) {
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
}

function getYouTubeId(url: string) {
    const m = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&?/]+)/);
    return m ? m[1] : null;
}

const STAGE_LABEL: Record<string, string> = {
    downloading: "Preparing video…",
    transcribing: "Extracting captions…",
    analyzing: "AI analyzing…",
    processing: "Reframing & rendering clips…",
    done: "Complete",
    error: "Failed",
};

export default function ProjectPage() {
    const params = useParams();
    const id = params.id as string;

    const [project, setProject] = useState<(Project & { clips: Clip[] }) | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedClip, setSelectedClip] = useState<Clip | null>(null);
    const [wsProgress, setWsProgress] = useState<{ stage: string; percent: number; message: string } | null>(null);
    const [logs, setLogs] = useState<string[]>([]);
    const [retrying, setRetrying] = useState(false);
    const [ytPublishClip, setYtPublishClip] = useState<Clip | null>(null);
    const [showBatchYt, setShowBatchYt] = useState(false);
    const [relayoutMode, setRelayoutMode] = useState("podcast_split");
    const [relayoutingAll, setRelayoutingAll] = useState(false);
    const [relayoutMsg, setRelayoutMsg] = useState<string | null>(null);

    const handleRelayoutAll = async () => {
        if (!project || relayoutingAll) return;
        setRelayoutingAll(true);
        setRelayoutMsg(null);
        try {
            const updated = await relayoutProject(id, relayoutMode);
            setProject(updated);
            if (selectedClip) {
                const match = updated.clips?.find((c) => c.id === selectedClip.id);
                if (match) setSelectedClip(match);
            } else if (updated.clips?.length > 0) {
                setSelectedClip(updated.clips[0]);
            }
            setRelayoutMsg(`✓ Re-reframed all ${updated.clips?.length || 0} clips with "${relayoutMode}".`);
        } catch (err) {
            setRelayoutMsg(err instanceof Error ? err.message : "Re-layout failed");
        } finally {
            setRelayoutingAll(false);
        }
    };

    const refreshProject = () => {
        getProject(id)
            .then((data) => {
                setProject(data);
                if (selectedClip) {
                    const updated = data.clips?.find((c) => c.id === selectedClip.id);
                    if (updated) setSelectedClip(updated);
                } else if (data.clips?.length > 0) {
                    setSelectedClip(data.clips[0]);
                }
            })
            .catch(console.error);
    };

    useEffect(() => {
        getProject(id)
            .then((data) => {
                setProject(data);
                if (data.clips?.length > 0) setSelectedClip(data.clips[0]);
            })
            .catch((err) => setError(err instanceof Error ? err.message : "Not found"))
            .finally(() => setLoading(false));
    }, [id]);

    const handleRetry = async () => {
        if (!project || retrying) return;
        setRetrying(true);
        try {
            await retryProject(id);
            setWsProgress(null);
            setLogs([]);
            setProject({ ...project, status: "pending", error_message: null });
        } catch {
            setProject({ ...project, error_message: "Could not restart the project. Is the backend running?" });
        } finally {
            setRetrying(false);
        }
    };

    useEffect(() => {
        if (!project || ["done", "error"].includes(project.status)) return;
        const baseUrl = getApiBaseUrl();
        const wsBase = baseUrl.replace(/^https/, "wss").replace(/^http/, "ws");
        const ws = new WebSocket(`${wsBase}/ws/progress/${id}`);
        ws.onmessage = (e) => {
            try {
                const d = JSON.parse(e.data);
                setWsProgress({ stage: d.stage, percent: d.percent, message: d.message });
                setLogs((prev) => [...prev.slice(-49), `[${new Date().toLocaleTimeString()}] ${d.message}`]);
                if (d.stage === "done") {
                    ws.close();
                    window.location.reload();
                }
                if (d.stage === "error") {
                    ws.close();
                    setProject((p) => p && { ...p, status: "error", error_message: d.message });
                }
            } catch {
                /* ignore */
            }
        };
        return () => ws.close();
    }, [project, id]);

    const ytId = project ? getYouTubeId(project.youtube_url) : null;
    const isProcessing = project && !["done", "error"].includes(project.status);
    const pct = wsProgress?.percent ?? 0;
    const clampedPct = Math.min(100, Math.max(0, pct));

    if (loading) {
        return (
            <div className="flex h-[calc(100vh-73px)] items-center justify-center">
                <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                    <p className="text-slate-400 text-sm">Loading project…</p>
                </div>
            </div>
        );
    }

    if (error || !project) {
        return (
            <div className="flex h-[calc(100vh-73px)] items-center justify-center flex-col gap-4">
                <p className="text-red-400 font-semibold">{error || "Project not found"}</p>
                <Link href="/" className="text-sm text-primary hover:underline">
                    ← Back to Dashboard
                </Link>
            </div>
        );
    }

    return (
        <div className="flex h-[calc(100vh-73px)] overflow-hidden">
            {/* Left: Video + Meta */}
            <section className="flex-[2] flex flex-col gap-6 overflow-y-auto custom-scrollbar p-8">
                {/* Header */}
                <div className="flex items-start justify-between gap-4 flex-wrap">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2 mb-1.5">
                            <span className="text-xs font-medium text-slate-500 uppercase tracking-widest">
                                Project / {id.slice(0, 8).toUpperCase()}
                            </span>
                            {project.source_type === "upload" && (
                                <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                                    Local Upload
                                </span>
                            )}
                        </div>
                        <h1 className="text-2xl font-bold text-white truncate">
                            {project.title || "Untitled Project"}
                        </h1>
                    </div>
                    <div className="flex gap-2.5 flex-shrink-0">
                        <Link
                            href="/"
                            className="px-4 py-2 rounded-xl glass glass-hover text-sm font-semibold flex items-center gap-2 text-slate-300"
                        >
                            ← Dashboard
                        </Link>
                        {selectedClip && (
                            <Link
                                href={`/project/${id}/edit/${selectedClip.id}`}
                                className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-bold flex items-center gap-2 transition-all"
                            >
                                <Sliders className="h-4 w-4" />
                                Edit Clip in Studio
                            </Link>
                        )}
                        {project.clips?.length > 0 && (
                            <>
                                <div className="flex items-center gap-1.5 rounded-xl bg-white/5 border border-white/10 p-1">
                                    <select
                                        value={relayoutMode}
                                        onChange={(e) => setRelayoutMode(e.target.value)}
                                        disabled={relayoutingAll}
                                        className="bg-transparent text-xs font-semibold text-slate-200 px-2 py-1 focus:outline-none"
                                    >
                                        <option value="auto" className="bg-slate-900">Auto Detect</option>
                                        <option value="podcast_split" className="bg-slate-900">Podcast Split-Screen</option>
                                        <option value="podcast_switch" className="bg-slate-900">Podcast Active Speaker</option>
                                        <option value="dynamic_zoom" className="bg-slate-900">Smart Face Track</option>
                                        <option value="tutorial" className="bg-slate-900">Tutorial (Screen+Face)</option>
                                        <option value="original" className="bg-slate-900">Original + Blur</option>
                                    </select>
                                    <button
                                        type="button"
                                        onClick={handleRelayoutAll}
                                        disabled={relayoutingAll}
                                        className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-xs font-bold text-white flex items-center gap-1.5 transition-colors"
                                        title="Re-reframe all clips in this project (auto re-downloads YouTube source if deleted)"
                                    >
                                        {relayoutingAll ? (
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                        ) : (
                                            <LayoutTemplate className="h-3.5 w-3.5" />
                                        )}
                                        {relayoutingAll ? "Re-reframing…" : "Re-reframe All"}
                                    </button>
                                </div>
                                <button
                                    type="button"
                                    onClick={() => setShowBatchYt(true)}
                                    className="px-4 py-2 rounded-xl bg-red-600/20 hover:bg-red-600/30 border border-red-500/40 text-red-300 hover:text-white text-sm font-bold flex items-center gap-2 transition-all"
                                >
                                    <Calendar className="h-4 w-4" />
                                    Auto-Schedule YT
                                </button>
                                <a
                                    href={getExportAllUrl(id)}
                                    download
                                    className="px-4 py-2 rounded-xl bg-primary hover:bg-primary/90 text-white text-sm font-bold flex items-center gap-2 glow-primary transition-all"
                                >
                                    <Archive className="h-4 w-4" />
                                    Export All (.zip)
                                </a>
                            </>
                        )}
                    </div>
                </div>

                {relayoutMsg && (
                    <div className={`rounded-2xl px-4 py-3 text-xs font-semibold border ${
                        relayoutMsg.startsWith("✓")
                            ? "bg-emerald-500/10 border-emerald-500/25 text-emerald-300"
                            : "bg-red-500/10 border-red-500/25 text-red-300"
                    }`}>
                        {relayoutMsg}
                    </div>
                )}

                {/* Video Player */}
                <div className="relative aspect-video rounded-3xl overflow-hidden glass border border-white/5 shadow-2xl bg-black">
                    {selectedClip ? (
                        <video
                            src={`${getApiBaseUrl()}${selectedClip.file_path}`}
                            controls
                            className="w-full h-full object-contain"
                            key={`${selectedClip.id}-${selectedClip.file_path}`}
                        />
                    ) : ytId ? (
                        <iframe
                            src={`https://www.youtube.com/embed/${ytId}`}
                            className="w-full h-full"
                            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                            allowFullScreen
                        />
                    ) : (
                        <div className="absolute inset-0 flex flex-col items-center justify-center gap-4">
                            {isProcessing ? (
                                <>
                                    <div className="h-10 w-10 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                                    <p className="text-slate-400 text-sm">
                                        {STAGE_LABEL[wsProgress?.stage || project.status] || "Processing…"}
                                    </p>
                                </>
                            ) : (
                                <div className="flex flex-col items-center gap-2 text-slate-600">
                                    <Play className="h-12 w-12" />
                                    <p className="text-sm">No video available</p>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Selected Clip Insights Strip */}
                {selectedClip && (
                    <div className="glass-card rounded-2xl p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 border border-white/10">
                        <div className="space-y-1.5 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                                <h2 className="text-base font-bold text-white truncate">
                                    {selectedClip.title || "Selected Clip"}
                                </h2>
                                {selectedClip.layout_mode && (
                                    <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded bg-white/10 text-slate-300">
                                        {selectedClip.layout_mode}
                                    </span>
                                )}
                                {selectedClip.viral_score != null && (
                                    <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded bg-primary/20 text-indigo-300">
                                        Viral {selectedClip.viral_score}/10
                                    </span>
                                )}
                            </div>
                            {selectedClip.reason && (
                                <p className="text-xs text-slate-400">{selectedClip.reason}</p>
                            )}
                            {selectedClip.hashtags && selectedClip.hashtags.length > 0 && (
                                <div className="flex flex-wrap gap-1.5 pt-1">
                                    {selectedClip.hashtags.map((h, i) => (
                                        <span key={i} className="text-[11px] text-indigo-300 bg-primary/10 px-2 py-0.5 rounded-full">
                                            {h}
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0 flex-wrap">
                            {selectedClip.youtube_url ? (
                                <a
                                    href={selectedClip.youtube_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="px-3.5 py-2.5 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-xs font-bold text-emerald-300 flex items-center gap-1.5"
                                >
                                    <CheckCircle2 className="h-3.5 w-3.5" />
                                    On YouTube ({selectedClip.youtube_status || "posted"})
                                </a>
                            ) : (
                                <button
                                    type="button"
                                    onClick={() => setYtPublishClip(selectedClip)}
                                    className="px-3.5 py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-xs font-bold text-white flex items-center gap-1.5 transition-colors"
                                >
                                    <Youtube className="h-3.5 w-3.5" />
                                    Post to YouTube
                                </button>
                            )}
                            <Link
                                href={`/project/${id}/edit/${selectedClip.id}`}
                                className="px-4 py-2.5 rounded-xl bg-primary hover:bg-primary/90 text-xs font-bold text-white flex items-center gap-1.5"
                            >
                                <Sliders className="h-3.5 w-3.5" />
                                Open Studio Editor
                            </Link>
                            <a
                                href={`${getApiBaseUrl()}${selectedClip.file_path}`}
                                download
                                className="px-3.5 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-xs font-bold text-slate-200 flex items-center gap-1.5"
                            >
                                <Download className="h-3.5 w-3.5" />
                                MP4
                            </a>
                        </div>
                    </div>
                )}

                {/* Failed run */}
                {!isProcessing && (project.status === "error" || project.error_message) && (
                    <div className={`glass-card rounded-2xl p-6 border ${project.status === "error" ? "border-red-500/20" : "border-amber-500/20"}`}>
                        <p className="font-bold text-white">
                            {project.status === "error" ? "This project didn't finish" : "Some clips were skipped"}
                        </p>
                        <p className="mt-1 text-sm text-slate-400 break-words">
                            {project.error_message || "Something went wrong while processing this video."}
                        </p>
                        <button
                            type="button"
                            onClick={handleRetry}
                            disabled={retrying}
                            className="mt-4 rounded-xl bg-primary px-5 py-2.5 text-sm font-bold text-white glow-primary hover:bg-primary/90 transition-all disabled:opacity-60"
                        >
                            {retrying ? "Restarting…" : "Retry"}
                        </button>
                    </div>
                )}

                {/* Live Progress */}
                {isProcessing && (
                    <div className="glass-card rounded-2xl p-6 space-y-4">
                        <div className="flex justify-between items-center">
                            <p className="text-slate-100 font-semibold text-sm">
                                {STAGE_LABEL[wsProgress?.stage || project.status] || "Processing…"}
                            </p>
                            <p className="text-primary font-bold">{Math.round(clampedPct)}%</p>
                        </div>
                        <div className="h-2 w-full bg-white/5 rounded-full overflow-hidden">
                            <div
                                className="h-full rounded-full transition-all duration-500"
                                style={{ width: `${clampedPct}%`, background: "linear-gradient(to right, #2e1ded, #8b5cf6)" }}
                            />
                        </div>
                        <div className="bg-[#0a0a0a] rounded-xl p-4 font-mono text-xs space-y-1 max-h-32 overflow-y-auto custom-scrollbar border border-white/5">
                            {logs.length === 0 ? (
                                <span className="text-slate-600 italic">Connecting…</span>
                            ) : (
                                logs.map((log, i) => (
                                    <p key={i} className="text-slate-400">{log}</p>
                                ))
                            )}
                        </div>
                    </div>
                )}

                {/* Video Metadata */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                    {[
                        { label: "Status", val: project.status.charAt(0).toUpperCase() + project.status.slice(1) },
                        { label: "Clips", val: `${project.clips?.length || 0} generated` },
                        { label: "Source", val: project.source_type === "upload" ? "Local Upload" : ytId ? `YouTube: ${ytId}` : "Video" },
                        { label: "Created", val: new Date(project.created_at).toLocaleDateString() },
                    ].map(({ label, val }) => (
                        <div key={label} className="glass rounded-2xl p-4">
                            <p className="text-xs text-slate-500 mb-1">{label}</p>
                            <p className="text-sm font-bold text-white truncate">{val}</p>
                        </div>
                    ))}
                </div>

                {ytId && (
                    <a href={project.youtube_url} target="_blank" rel="noreferrer" className="text-sm text-primary hover:underline">
                        View original YouTube video →
                    </a>
                )}
            </section>

            {/* Right: Clips Sidebar */}
            <aside className="w-84 max-w-[340px] w-full flex-shrink-0 border-l border-white/5 flex flex-col">
                <div className="p-5 border-b border-white/5 glass-dark flex items-center justify-between">
                    <h3 className="font-bold text-white">Generated Clips</h3>
                    {project.clips?.length > 0 && (
                        <span className="text-xs font-bold px-2.5 py-1 rounded-lg bg-primary/20 text-primary border border-primary/30">
                            {project.clips.length} TOTAL
                        </span>
                    )}
                </div>

                <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-3">
                    {project.clips?.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-600">
                            <p className="text-sm">
                                {isProcessing ? "Processing clips…" : "No clips generated"}
                            </p>
                        </div>
                    ) : (
                        project.clips.map((clip, i) => {
                            const isSelected = selectedClip?.id === clip.id;
                            const viralPct = clip.viral_score ? (clip.viral_score / 10) * 100 : 0;
                            return (
                                <div
                                    key={clip.id}
                                    onClick={() => setSelectedClip(clip)}
                                    className={`glass rounded-2xl p-3.5 flex gap-3.5 group cursor-pointer transition-all ${
                                        isSelected ? "border border-primary/60 bg-primary/10" : "border border-white/5 hover:border-primary/30"
                                    }`}
                                >
                                    <div className="relative w-16 aspect-[9/16] rounded-xl overflow-hidden flex-shrink-0 bg-slate-900 flex items-center justify-center">
                                        <Play className={`h-5 w-5 ${isSelected ? "text-primary" : "text-white/50"}`} />
                                        <span className="absolute top-1 left-1 rounded px-1 py-0.5 text-[9px] font-bold bg-primary text-white">
                                            {formatDuration(clip.duration)}
                                        </span>
                                    </div>
                                    <div className="flex-1 flex flex-col justify-between min-w-0">
                                        <div>
                                            <h4 className="text-xs font-bold text-white line-clamp-2 leading-snug">
                                                {clip.title || `Clip ${i + 1}`}
                                            </h4>
                                            {clip.viral_score != null && (
                                                <div className="mt-1.5 flex items-center gap-1.5">
                                                    <div className="flex-1 h-1 bg-white/10 rounded-full">
                                                        <div
                                                            className="h-full rounded-full bg-gradient-to-r from-primary to-accent-purple"
                                                            style={{ width: `${viralPct}%` }}
                                                        />
                                                    </div>
                                                    <span className="text-[10px] font-bold text-indigo-300">
                                                        {clip.viral_score}/10
                                                    </span>
                                                </div>
                                            )}
                                        </div>
                                        <div className="flex gap-1.5 mt-2.5">
                                            <Link
                                                href={`/project/${id}/edit/${clip.id}`}
                                                onClick={(e) => e.stopPropagation()}
                                                className="flex-1 py-1.5 rounded-lg bg-primary hover:bg-primary/90 text-[10px] font-bold text-center text-white transition-colors"
                                            >
                                                EDIT
                                            </Link>
                                            <a
                                                href={`${getApiBaseUrl()}${clip.file_path}`}
                                                download
                                                onClick={(e) => e.stopPropagation()}
                                                className="px-2.5 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-[10px] font-bold border border-white/10 text-center"
                                            >
                                                MP4
                                            </a>
                                        </div>
                                    </div>
                                </div>
                            );
                        })
                    )}
                </div>

                {project.status === "done" && (
                    <div className="p-4 border-t border-white/5">
                        <Link
                            href="/create"
                            className="flex items-center justify-center gap-2 w-full py-3 rounded-xl bg-primary hover:bg-primary/90 text-white text-sm font-bold glow-primary transition-all"
                        >
                            + New Video Project
                        </Link>
                    </div>
                )}
            </aside>

            {ytPublishClip && (
                <YouTubePublishModal
                    mode="single"
                    clip={ytPublishClip}
                    onClose={() => setYtPublishClip(null)}
                    onPublished={() => refreshProject()}
                />
            )}
            {showBatchYt && (
                <YouTubePublishModal
                    mode="batch"
                    projectId={id}
                    clipCount={project.clips?.length || 0}
                    unpostedCount={(project.clips || []).filter((c) => !c.youtube_video_id).length}
                    onClose={() => setShowBatchYt(false)}
                    onScheduled={() => refreshProject()}
                />
            )}
        </div>
    );
}
