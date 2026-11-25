"use client";

import { useRef, useState } from "react";
import {
    Scissors,
    Merge,
    ZoomIn,
    ZoomOut,
    Trash2,
    PlusCircle,
} from "lucide-react";
import { ClipEditState } from "@/lib/types";

interface EditorTimelineProps {
    editState: ClipEditState;
    duration: number;
    currentTime: number;
    waveform: number[];
    selectedBlockId: string | null;
    selectedTrack: "captions" | "logo" | "video";
    onSeek: (time: number) => void;
    onSelectBlock: (blockId: string) => void;
    onSelectTrack: (track: "captions" | "logo" | "video") => void;
    onUpdateTrim: (trimStart: number, trimEnd: number) => void;
    onUpdateBlockTiming: (blockId: string, start: number, end: number) => void;
    onSplitBlockAtPlayhead: () => void;
    onMergeWithNextBlock: () => void;
    onDeleteSelectedBlock: () => void;
    onAddCutAtPlayhead: () => void;
}

export function EditorTimeline({
    editState,
    duration,
    currentTime,
    waveform,
    selectedBlockId,
    selectedTrack,
    onSeek,
    onSelectBlock,
    onSelectTrack,
    onUpdateTrim,
    onUpdateBlockTiming,
    onSplitBlockAtPlayhead,
    onMergeWithNextBlock,
    onDeleteSelectedBlock,
    onAddCutAtPlayhead,
}: EditorTimelineProps) {
    const [zoom, setZoom] = useState(1.5);
    const tracksRef = useRef<HTMLDivElement>(null);
    const [dragMode, setDragMode] = useState<
        | null
        | { type: "playhead" }
        | { type: "trim-start" }
        | { type: "trim-end" }
        | { type: "blk-start"; id: string; origEnd: number }
        | { type: "blk-end"; id: string; origStart: number }
    >(null);

    const safeDur = Math.max(duration || editState.trim_end || 30, 1);
    const pxPerSec = 28 * zoom;
    const timelineWidth = Math.max(760, Math.ceil(safeDur * pxPerSec));

    const timeFromClientX = (clientX: number): number => {
        if (!tracksRef.current) return 0;
        const rect = tracksRef.current.getBoundingClientRect();
        const x = clientX - rect.left + tracksRef.current.scrollLeft;
        return Math.max(0, Math.min(safeDur, x / pxPerSec));
    };

    const handlePointerMove = (e: React.PointerEvent) => {
        if (!dragMode) return;
        const t = timeFromClientX(e.clientX);
        if (dragMode.type === "playhead") {
            onSeek(t);
        } else if (dragMode.type === "trim-start") {
            onUpdateTrim(Math.min(t, editState.trim_end - 0.5), editState.trim_end);
        } else if (dragMode.type === "trim-end") {
            onUpdateTrim(editState.trim_start, Math.max(t, editState.trim_start + 0.5));
        } else if (dragMode.type === "blk-start") {
            onUpdateBlockTiming(dragMode.id, Math.min(t, dragMode.origEnd - 0.1), dragMode.origEnd);
        } else if (dragMode.type === "blk-end") {
            onUpdateBlockTiming(dragMode.id, dragMode.origStart, Math.max(t, dragMode.origStart + 0.1));
        }
    };

    // Compute kept output duration
    const cutTotal = editState.cuts.reduce(
        (acc, c) => acc + Math.max(0, c.end - c.start),
        0,
    );
    const keptDur = Math.max(0.5, editState.trim_end - editState.trim_start - cutTotal);

    // Ruler ticks
    const tickStep = zoom >= 3 ? 1 : zoom >= 1.5 ? 2 : 5;
    const ticks: number[] = [];
    for (let s = 0; s <= safeDur; s += tickStep) {
        ticks.push(s);
    }

    return (
        <div
            className="h-[235px] flex-shrink-0 border-t border-white/10 bg-[#07070b] flex flex-col select-none"
            onPointerMove={handlePointerMove}
            onPointerUp={() => setDragMode(null)}
            onPointerLeave={() => setDragMode(null)}
        >
            {/* Timeline Action Toolbar */}
            <div className="h-9 flex-shrink-0 border-b border-white/10 px-4 flex items-center justify-between bg-[#0a0a10]">
                <div className="flex items-center gap-1.5">
                    <button
                        type="button"
                        onClick={onSplitBlockAtPlayhead}
                        title="Split caption block at playhead (S)"
                        className="h-6 px-2.5 rounded-md bg-white/5 hover:bg-white/10 text-[11px] font-semibold text-slate-300 flex items-center gap-1"
                    >
                        <Scissors className="h-3 w-3" />
                        Split Caption
                    </button>
                    <button
                        type="button"
                        onClick={onMergeWithNextBlock}
                        disabled={!selectedBlockId}
                        className="h-6 px-2.5 rounded-md bg-white/5 hover:bg-white/10 disabled:opacity-35 text-[11px] font-semibold text-slate-300 flex items-center gap-1"
                    >
                        <Merge className="h-3 w-3" />
                        Merge Next
                    </button>
                    <button
                        type="button"
                        onClick={onDeleteSelectedBlock}
                        disabled={!selectedBlockId}
                        className="h-6 px-2 rounded-md bg-white/5 hover:bg-red-500/20 disabled:opacity-35 text-[11px] font-semibold text-slate-300 hover:text-red-300 flex items-center gap-1"
                    >
                        <Trash2 className="h-3 w-3" />
                    </button>
                    <div className="w-px h-4 bg-white/10 mx-1" />
                    <button
                        type="button"
                        onClick={onAddCutAtPlayhead}
                        className="h-6 px-2.5 rounded-md bg-red-500/15 hover:bg-red-500/25 border border-red-500/30 text-[11px] font-semibold text-red-300 flex items-center gap-1"
                    >
                        <PlusCircle className="h-3 w-3" />
                        Cut 1s at Playhead
                    </button>
                </div>

                <div className="flex items-center gap-3">
                    <span className="text-[11px] font-mono text-slate-400">
                        Output:{" "}
                        <strong className="text-emerald-400">{keptDur.toFixed(1)}s</strong>
                    </span>
                    <div className="flex items-center gap-1.5">
                        <ZoomOut className="h-3.5 w-3.5 text-slate-500" />
                        <input
                            type="range"
                            min={0.6}
                            max={5}
                            step={0.2}
                            value={zoom}
                            onChange={(e) => setZoom(Number(e.target.value))}
                            className="w-20 accent-primary"
                        />
                        <ZoomIn className="h-3.5 w-3.5 text-slate-500" />
                    </div>
                </div>
            </div>

            {/* Track Labels + Scrollable Multi-Track Canvas */}
            <div className="flex-1 flex overflow-hidden">
                {/* Fixed Left Track Headers */}
                <div className="w-24 flex-shrink-0 border-r border-white/10 bg-[#050508] flex flex-col">
                    <div className="h-6 border-b border-white/5" />
                    <button
                        type="button"
                        onClick={() => onSelectTrack("logo")}
                        className={`h-8 px-2.5 text-left text-[10px] font-bold uppercase tracking-wider border-b border-white/5 flex items-center justify-between ${
                            selectedTrack === "logo" ? "text-purple-300 bg-purple-500/10" : "text-slate-500"
                        }`}
                    >
                        <span>V3 Logo</span>
                    </button>
                    <button
                        type="button"
                        onClick={() => onSelectTrack("captions")}
                        className={`h-11 px-2.5 text-left text-[10px] font-bold uppercase tracking-wider border-b border-white/5 flex items-center justify-between ${
                            selectedTrack === "captions" ? "text-indigo-300 bg-primary/10" : "text-slate-500"
                        }`}
                    >
                        <span>V2 Caps</span>
                    </button>
                    <button
                        type="button"
                        onClick={() => onSelectTrack("video")}
                        className={`h-11 px-2.5 text-left text-[10px] font-bold uppercase tracking-wider border-b border-white/5 flex items-center justify-between ${
                            selectedTrack === "video" ? "text-emerald-300 bg-emerald-500/10" : "text-slate-500"
                        }`}
                    >
                        <span>V1 Video</span>
                    </button>
                    <div className="flex-1 px-2.5 text-left text-[10px] font-bold uppercase tracking-wider text-slate-500 flex items-center">
                        A1 Audio
                    </div>
                </div>

                {/* Scrollable Timeline Tracks */}
                <div
                    ref={tracksRef}
                    className="flex-1 overflow-x-auto overflow-y-hidden custom-scrollbar relative"
                >
                    <div style={{ width: `${timelineWidth}px` }} className="h-full relative flex flex-col">
                        {/* Time Ruler */}
                        <div
                            onPointerDown={(e) => {
                                onSeek(timeFromClientX(e.clientX));
                                setDragMode({ type: "playhead" });
                            }}
                            className="h-6 border-b border-white/10 relative cursor-pointer bg-[#0a0a10]"
                        >
                            {ticks.map((sec) => (
                                <div
                                    key={sec}
                                    style={{ left: `${sec * pxPerSec}px` }}
                                    className="absolute top-0 bottom-0 border-l border-white/10 pl-1 text-[9px] font-mono text-slate-500 flex items-center"
                                >
                                    {sec}s
                                </div>
                            ))}
                        </div>

                        {/* Track V3: Logo */}
                        <div
                            onClick={() => onSelectTrack("logo")}
                            className="h-8 border-b border-white/5 relative flex items-center"
                        >
                            {editState.logo.enabled && editState.logo.asset_url && (
                                <div
                                    style={{
                                        left: `${editState.trim_start * pxPerSec}px`,
                                        width: `${Math.max(
                                            20,
                                            (editState.trim_end - editState.trim_start) * pxPerSec,
                                        )}px`,
                                    }}
                                    className="absolute h-5 rounded-md bg-purple-500/25 border border-purple-400/50 px-2 flex items-center text-[10px] font-bold text-purple-200 truncate"
                                >
                                    Brand Logo Overlay
                                </div>
                            )}
                        </div>

                        {/* Track V2: Captions */}
                        <div className="h-11 border-b border-white/5 relative">
                            {editState.caption_enabled &&
                                editState.caption_blocks.map((blk) => {
                                    const left = blk.start * pxPerSec;
                                    const width = Math.max(18, (blk.end - blk.start) * pxPerSec);
                                    const isSel = selectedBlockId === blk.id;
                                    return (
                                        <div
                                            key={blk.id}
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                onSelectTrack("captions");
                                                onSelectBlock(blk.id);
                                                onSeek(blk.start);
                                            }}
                                            style={{ left: `${left}px`, width: `${width}px` }}
                                            className={`absolute top-1.5 bottom-1.5 rounded-md px-2 flex items-center overflow-hidden cursor-pointer border transition-colors ${
                                                isSel
                                                    ? "bg-primary/40 border-indigo-400 text-white z-10"
                                                    : "bg-indigo-500/20 border-indigo-500/40 text-indigo-100 hover:bg-indigo-500/30"
                                            }`}
                                        >
                                            {/* Left resize handle */}
                                            <div
                                                onPointerDown={(e) => {
                                                    e.stopPropagation();
                                                    onSelectBlock(blk.id);
                                                    setDragMode({
                                                        type: "blk-start",
                                                        id: blk.id,
                                                        origEnd: blk.end,
                                                    });
                                                }}
                                                className="absolute left-0 top-0 bottom-0 w-1.5 cursor-ew-resize hover:bg-white/60"
                                            />
                                            <span className="text-[10px] font-semibold truncate pointer-events-none">
                                                {blk.emoji ? `${blk.emoji} ` : ""}
                                                {blk.text}
                                            </span>
                                            {/* Right resize handle */}
                                            <div
                                                onPointerDown={(e) => {
                                                    e.stopPropagation();
                                                    onSelectBlock(blk.id);
                                                    setDragMode({
                                                        type: "blk-end",
                                                        id: blk.id,
                                                        origStart: blk.start,
                                                    });
                                                }}
                                                className="absolute right-0 top-0 bottom-0 w-1.5 cursor-ew-resize hover:bg-white/60"
                                            />
                                        </div>
                                    );
                                })}
                        </div>

                        {/* Track V1: Video Trim & Cuts */}
                        <div
                            onClick={() => onSelectTrack("video")}
                            className="h-11 border-b border-white/5 relative"
                        >
                            {/* Active Kept Video Bar */}
                            <div
                                style={{
                                    left: `${editState.trim_start * pxPerSec}px`,
                                    width: `${Math.max(
                                        20,
                                        (editState.trim_end - editState.trim_start) * pxPerSec,
                                    )}px`,
                                }}
                                className="absolute top-1.5 bottom-1.5 rounded-md bg-emerald-500/20 border border-emerald-500/50 flex items-center px-3"
                            >
                                <span className="text-[10px] font-bold text-emerald-200 truncate">
                                    9:16 Video ({editState.layout_mode})
                                </span>

                                {/* Left Trim Handle */}
                                <div
                                    onPointerDown={(e) => {
                                        e.stopPropagation();
                                        onSelectTrack("video");
                                        setDragMode({ type: "trim-start" });
                                    }}
                                    title="Drag to trim start"
                                    className="absolute -left-1 top-0 bottom-0 w-2.5 rounded-l bg-emerald-400 cursor-ew-resize hover:bg-white"
                                />
                                {/* Right Trim Handle */}
                                <div
                                    onPointerDown={(e) => {
                                        e.stopPropagation();
                                        onSelectTrack("video");
                                        setDragMode({ type: "trim-end" });
                                    }}
                                    title="Drag to trim end"
                                    className="absolute -right-1 top-0 bottom-0 w-2.5 rounded-r bg-emerald-400 cursor-ew-resize hover:bg-white"
                                />
                            </div>

                            {/* Red Cut Overlays */}
                            {editState.cuts.map((c) => (
                                <div
                                    key={c.id}
                                    style={{
                                        left: `${c.start * pxPerSec}px`,
                                        width: `${Math.max(6, (c.end - c.start) * pxPerSec)}px`,
                                    }}
                                    title={`Cut (${c.reason || "cut"}): ${c.start.toFixed(1)}s - ${c.end.toFixed(1)}s`}
                                    className="absolute top-1.5 bottom-1.5 bg-red-500/45 border border-red-400/80 z-10 pointer-events-none"
                                />
                            ))}
                        </div>

                        {/* Track A1: Real Audio Waveform */}
                        <div
                            onPointerDown={(e) => {
                                onSeek(timeFromClientX(e.clientX));
                                setDragMode({ type: "playhead" });
                            }}
                            className="flex-1 relative flex items-center cursor-pointer px-0.5"
                        >
                            <div className="w-full h-10 flex items-center gap-[1px]">
                                {waveform.map((amp, i) => (
                                    <div
                                        key={i}
                                        style={{ height: `${Math.max(12, Math.round(amp * 100))}%` }}
                                        className="flex-1 rounded-full bg-cyan-400/55 min-w-[1px]"
                                    />
                                ))}
                            </div>
                        </div>

                        {/* Global Playhead Needle */}
                        <div
                            style={{ left: `${currentTime * pxPerSec}px` }}
                            className="absolute top-0 bottom-0 w-[2px] bg-rose-500 z-30 pointer-events-none"
                        >
                            <div className="w-3 h-3 -ml-[5px] -mt-0.5 rounded-b-sm bg-rose-500 shadow" />
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
