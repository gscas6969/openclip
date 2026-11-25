"use client";

import Link from "next/link";
import {
    ArrowLeft,
    Undo2,
    Redo2,
    Download,
    Sparkles,
    Check,
    Loader2,
    Smartphone,
    AlertCircle,
    Youtube,
} from "lucide-react";

interface EditorTopBarProps {
    projectId: string;
    title: string;
    onTitleChange: (title: string) => void;
    saveStatus: "saved" | "saving" | "unsaved" | "error";
    canUndo: boolean;
    canRedo: boolean;
    onUndo: () => void;
    onRedo: () => void;
    showSafeZones: boolean;
    onToggleSafeZones: () => void;
    isRendering: boolean;
    onRender: () => void;
    onOpenYouTube: () => void;
    downloadUrl: string;
}

export function EditorTopBar({
    projectId,
    title,
    onTitleChange,
    saveStatus,
    canUndo,
    canRedo,
    onUndo,
    onRedo,
    showSafeZones,
    onToggleSafeZones,
    isRendering,
    onRender,
    onOpenYouTube,
    downloadUrl,
}: EditorTopBarProps) {
    return (
        <div className="h-14 flex-shrink-0 border-b border-white/10 bg-[#08080c] px-4 flex items-center justify-between gap-4">
            {/* Left: Back + Editable Title */}
            <div className="flex items-center gap-3 min-w-0 flex-1">
                <Link
                    href={`/project/${projectId}`}
                    className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/5 hover:bg-white/10 text-slate-300 hover:text-white transition-colors flex-shrink-0"
                    title="Back to Project"
                >
                    <ArrowLeft className="h-4 w-4" />
                </Link>
                <div className="min-w-0 flex-1 max-w-md">
                    <input
                        value={title}
                        onChange={(e) => onTitleChange(e.target.value)}
                        className="w-full bg-transparent text-sm font-bold text-white focus:outline-none focus:bg-white/5 rounded-md px-2 py-1 truncate border border-transparent focus:border-white/15"
                        placeholder="Untitled Clip"
                    />
                </div>
                <span
                    className={`hidden sm:inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full border ${
                        saveStatus === "saved"
                            ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
                            : saveStatus === "saving"
                              ? "border-primary/30 bg-primary/10 text-indigo-300"
                              : saveStatus === "error"
                                ? "border-red-500/30 bg-red-500/10 text-red-400"
                                : "border-amber-500/20 bg-amber-500/10 text-amber-300"
                    }`}
                >
                    {saveStatus === "saved" && <Check className="h-3 w-3" />}
                    {saveStatus === "saving" && <Loader2 className="h-3 w-3 animate-spin" />}
                    {saveStatus === "error" && <AlertCircle className="h-3 w-3" />}
                    {saveStatus === "saved"
                        ? "Saved"
                        : saveStatus === "saving"
                          ? "Saving…"
                          : saveStatus === "error"
                            ? "Save failed"
                            : "Unsaved"}
                </span>
            </div>

            {/* Center: History & Viewer toggles */}
            <div className="flex items-center gap-1.5">
                <button
                    type="button"
                    onClick={onUndo}
                    disabled={!canUndo}
                    title="Undo (Ctrl+Z)"
                    className="h-8 w-8 rounded-lg flex items-center justify-center bg-white/5 hover:bg-white/10 text-slate-300 disabled:opacity-30 disabled:pointer-events-none transition-colors"
                >
                    <Undo2 className="h-4 w-4" />
                </button>
                <button
                    type="button"
                    onClick={onRedo}
                    disabled={!canRedo}
                    title="Redo (Ctrl+Shift+Z)"
                    className="h-8 w-8 rounded-lg flex items-center justify-center bg-white/5 hover:bg-white/10 text-slate-300 disabled:opacity-30 disabled:pointer-events-none transition-colors"
                >
                    <Redo2 className="h-4 w-4" />
                </button>
                <div className="w-px h-5 bg-white/10 mx-1" />
                <button
                    type="button"
                    onClick={onToggleSafeZones}
                    className={`h-8 px-3 rounded-lg flex items-center gap-1.5 text-xs font-semibold border transition-colors ${
                        showSafeZones
                            ? "bg-primary/20 border-primary/40 text-indigo-300"
                            : "bg-white/5 border-white/10 text-slate-400 hover:text-white"
                    }`}
                    title="Toggle TikTok / Reels Safe-Zone Guides"
                >
                    <Smartphone className="h-3.5 w-3.5" />
                    <span className="hidden md:inline">Safe Zones</span>
                </button>
            </div>

            {/* Right: Render + YouTube + Download */}
            <div className="flex items-center gap-2 flex-shrink-0">
                <a
                    href={downloadUrl}
                    download
                    className="h-9 px-3 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-xs font-bold text-slate-200 flex items-center gap-1.5 transition-colors"
                >
                    <Download className="h-3.5 w-3.5" />
                    <span className="hidden lg:inline">MP4</span>
                </a>
                <button
                    type="button"
                    onClick={onOpenYouTube}
                    className="h-9 px-3.5 rounded-xl bg-red-600/20 hover:bg-red-600 border border-red-500/40 text-xs font-bold text-red-200 hover:text-white flex items-center gap-1.5 transition-all"
                    title="Post or schedule this clip to YouTube Shorts"
                >
                    <Youtube className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">Post to YT</span>
                </button>
                <button
                    type="button"
                    onClick={onRender}
                    disabled={isRendering}
                    className="h-9 px-4 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-60 text-xs font-bold text-white flex items-center gap-2 glow-primary transition-all"
                >
                    {isRendering ? (
                        <>
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            Rendering…
                        </>
                    ) : (
                        <>
                            <Sparkles className="h-3.5 w-3.5" />
                            Render &amp; Export
                        </>
                    )}
                </button>
            </div>
        </div>
    );
}
