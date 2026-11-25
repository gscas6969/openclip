"use client";

import { useEffect, useRef, useState } from "react";
import {
    Play,
    Pause,
    SkipBack,
    SkipForward,
    Volume2,
    VolumeX,
} from "lucide-react";
import { ClipEditState } from "@/lib/types";
import { getApiBaseUrl } from "@/lib/api";

interface EditorViewerProps {
    videoUrl: string;
    editState: ClipEditState;
    currentTime: number;
    duration: number;
    isPlaying: boolean;
    playbackRate: number;
    showSafeZones: boolean;
    selectedTrack: "captions" | "logo" | "video";
    onTimeUpdate: (time: number) => void;
    onDurationLoaded: (dur: number) => void;
    onPlayStateChange: (playing: boolean) => void;
    onPlaybackRateChange: (rate: number) => void;
    onSelectTrack: (track: "captions" | "logo" | "video") => void;
    onUpdateCaptionY: (yPct: number) => void;
    onUpdateLogoPos: (xPct: number, yPct: number) => void;
    seekSignal: { time: number; seq: number } | null;
}

function formatTimecode(s: number): string {
    const clamped = Math.max(0, s);
    const mins = Math.floor(clamped / 60);
    const secs = Math.floor(clamped % 60);
    const cs = Math.floor((clamped % 1) * 100);
    return `${mins.toString().padStart(2, "0")}:${secs
        .toString()
        .padStart(2, "0")}.${cs.toString().padStart(2, "0")}`;
}

const SPEEDS = [0.25, 0.5, 1, 1.25, 1.5, 2];

export function EditorViewer({
    videoUrl,
    editState,
    currentTime,
    duration,
    isPlaying,
    playbackRate,
    showSafeZones,
    selectedTrack,
    onTimeUpdate,
    onDurationLoaded,
    onPlayStateChange,
    onPlaybackRateChange,
    onSelectTrack,
    onUpdateCaptionY,
    onUpdateLogoPos,
    seekSignal,
}: EditorViewerProps) {
    const videoRef = useRef<HTMLVideoElement>(null);
    const stageRef = useRef<HTMLDivElement>(null);
    const [muted, setMuted] = useState(false);
    const [draggingTarget, setDraggingTarget] = useState<"caption" | "logo" | null>(null);

    // Sync external seek signal
    useEffect(() => {
        if (!seekSignal || !videoRef.current) return;
        videoRef.current.currentTime = Math.max(0, seekSignal.time);
    }, [seekSignal]);

    // Sync playback rate
    useEffect(() => {
        if (videoRef.current) {
            videoRef.current.playbackRate = playbackRate;
        }
    }, [playbackRate]);

    // Sync play/pause
    useEffect(() => {
        const v = videoRef.current;
        if (!v) return;
        if (isPlaying && v.paused) {
            if (v.currentTime < editState.trim_start || v.currentTime >= editState.trim_end) {
                v.currentTime = editState.trim_start;
            }
            v.play().catch(() => onPlayStateChange(false));
        } else if (!isPlaying && !v.paused) {
            v.pause();
        }
    }, [isPlaying, editState.trim_start, editState.trim_end, onPlayStateChange]);

    // Real-time skip over cut segments & deleted words during playback
    const handleTimeUpdate = () => {
        const v = videoRef.current;
        if (!v) return;
        const t = v.currentTime;

        if (isPlaying) {
            if (t >= editState.trim_end) {
                v.pause();
                v.currentTime = editState.trim_start;
                onPlayStateChange(false);
                onTimeUpdate(editState.trim_start);
                return;
            }

            // Skip explicit cuts
            for (const c of editState.cuts) {
                if (t >= c.start && t < c.end - 0.03) {
                    v.currentTime = c.end;
                    onTimeUpdate(c.end);
                    return;
                }
            }

            // Skip deleted words
            for (const blk of editState.caption_blocks) {
                for (const w of blk.words) {
                    if (w.deleted && t >= w.start && t < w.end - 0.02) {
                        v.currentTime = w.end;
                        onTimeUpdate(w.end);
                        return;
                    }
                }
            }
        }

        onTimeUpdate(t);
    };

    const stepFrame = (dir: -1 | 1) => {
        const v = videoRef.current;
        if (!v) return;
        v.pause();
        onPlayStateChange(false);
        const next = Math.max(0, Math.min(duration || 999, v.currentTime + dir * (1 / 30)));
        v.currentTime = next;
        onTimeUpdate(next);
    };

    // Drag caption or logo directly inside the 9:16 stage
    const handlePointerMove = (e: React.PointerEvent) => {
        if (!draggingTarget || !stageRef.current) return;
        const rect = stageRef.current.getBoundingClientRect();
        const xPct = Math.round(Math.max(5, Math.min(95, ((e.clientX - rect.left) / rect.width) * 100)));
        const yPct = Math.round(Math.max(8, Math.min(92, ((e.clientY - rect.top) / rect.height) * 100)));
        if (draggingTarget === "caption") {
            onUpdateCaptionY(yPct);
        } else if (draggingTarget === "logo") {
            onUpdateLogoPos(xPct, yPct);
        }
    };

    // Find active caption block & word for real-time overlay (honoring sync_offset_ms)
    const ov = editState.caption_overrides;
    const syncShiftSec = (ov.sync_offset_ms ?? -120) / 1000;
    const syncedTime = currentTime - syncShiftSec;
    const activeBlock = editState.caption_enabled
        ? editState.caption_blocks.find(
              (b) => syncedTime >= b.start && syncedTime <= b.end + 0.08,
          )
        : undefined;
    const visibleWords = activeBlock
        ? activeBlock.words.filter((w) => !w.deleted)
        : [];
    const activeWordIdx = visibleWords.findIndex(
        (w, idx) =>
            syncedTime >= w.start &&
            (idx + 1 < visibleWords.length
                ? syncedTime < visibleWords[idx + 1].start
                : syncedTime <= w.end + 0.08),
    );

    const previewFontPx = Math.max(12, Math.round((ov.fontsize / 1080) * 290));
    const outlinePx = Math.max(0, Math.round(ov.outline_width * 0.35));
    const textShadow =
        ov.shadow > 0
            ? `0 ${Math.max(1, ov.shadow)}px ${ov.shadow * 2}px rgba(0,0,0,0.9)`
            : "none";
    const baseUrl = getApiBaseUrl();

    return (
        <section className="flex-1 flex flex-col bg-[#040407] overflow-hidden min-w-0">
            {/* 9:16 Stage Container */}
            <div
                className="flex-1 flex items-center justify-center p-4 overflow-hidden select-none"
                onPointerMove={handlePointerMove}
                onPointerUp={() => setDraggingTarget(null)}
                onPointerLeave={() => setDraggingTarget(null)}
            >
                <div
                    ref={stageRef}
                    className="relative h-full max-h-[540px] aspect-[9/16] rounded-2xl overflow-hidden bg-black border border-white/15 shadow-2xl"
                >
                    <video
                        ref={videoRef}
                        src={videoUrl}
                        muted={muted}
                        playsInline
                        onTimeUpdate={handleTimeUpdate}
                        onLoadedMetadata={(e) => {
                            const d = (e.target as HTMLVideoElement).duration;
                            if (d && Number.isFinite(d)) onDurationLoaded(d);
                        }}
                        onEnded={() => onPlayStateChange(false)}
                        onClick={() => onPlayStateChange(!isPlaying)}
                        className="w-full h-full object-cover cursor-pointer"
                    />

                    {/* TikTok / Reels Safe-Zone Overlay */}
                    {showSafeZones && (
                        <div className="pointer-events-none absolute inset-0 z-10">
                            <div className="absolute top-0 inset-x-0 h-[10%] bg-red-500/15 border-b border-dashed border-red-400/50 flex items-center justify-center">
                                <span className="text-[9px] font-bold uppercase tracking-widest text-red-300/80">
                                    Top UI Zone
                                </span>
                            </div>
                            <div className="absolute right-0 top-[42%] bottom-[22%] w-[18%] bg-red-500/15 border-l border-y border-dashed border-red-400/50 flex items-center justify-center">
                                <span className="text-[8px] font-bold uppercase -rotate-90 text-red-300/80 whitespace-nowrap">
                                    Likes / Share
                                </span>
                            </div>
                            <div className="absolute bottom-0 inset-x-0 h-[20%] bg-red-500/15 border-t border-dashed border-red-400/50 flex items-center justify-center">
                                <span className="text-[9px] font-bold uppercase tracking-widest text-red-300/80">
                                    Description / Handle Zone
                                </span>
                            </div>
                        </div>
                    )}

                    {/* Live Logo Overlay (Draggable) */}
                    {editState.logo.enabled && editState.logo.asset_url && (
                        <div
                            onPointerDown={(e) => {
                                e.stopPropagation();
                                onSelectTrack("logo");
                                setDraggingTarget("logo");
                            }}
                            style={{
                                left: `${editState.logo.x}%`,
                                top: `${editState.logo.y}%`,
                                width: `${editState.logo.scale}%`,
                                opacity: editState.logo.opacity / 100,
                                transform: "translate(-50%, -50%)",
                            }}
                            className={`absolute z-20 cursor-move rounded p-0.5 transition-shadow ${
                                selectedTrack === "logo"
                                    ? "ring-2 ring-primary ring-offset-1 ring-offset-black/40"
                                    : "hover:ring-1 hover:ring-white/40"
                            }`}
                        >
                            <img
                                src={`${baseUrl}${editState.logo.asset_url}`}
                                alt="Logo"
                                draggable={false}
                                className="w-full h-auto object-contain pointer-events-none"
                            />
                        </div>
                    )}

                    {/* Live Synced Kinetic Captions Overlay (Draggable Y) */}
                    {editState.caption_enabled && activeBlock && visibleWords.length > 0 && (
                        <div
                            onPointerDown={(e) => {
                                e.stopPropagation();
                                onSelectTrack("captions");
                                setDraggingTarget("caption");
                            }}
                            style={{
                                top: `${ov.position_y}%`,
                                transform: "translate(-50%, -50%)",
                                left: "50%",
                                width: "90%",
                            }}
                            className={`absolute z-20 text-center cursor-ns-resize px-2 py-1 rounded-xl transition-shadow ${
                                selectedTrack === "captions"
                                    ? "ring-1 ring-primary/70 bg-primary/5"
                                    : "hover:ring-1 hover:ring-white/30"
                            }`}
                        >
                            <div
                                className="inline-flex flex-wrap items-center justify-center gap-x-1.5 gap-y-0.5 leading-tight px-2.5 py-1 rounded-lg"
                                style={{
                                    fontFamily: ov.font,
                                    fontSize: `${previewFontPx}px`,
                                    fontWeight: 900,
                                    letterSpacing: `${ov.letter_spacing}px`,
                                    textTransform: ov.uppercase ? "uppercase" : "none",
                                    backgroundColor: ov.background ? ov.bg_color : "transparent",
                                    WebkitTextStroke:
                                        outlinePx > 0 ? `${outlinePx}px ${ov.outline_color}` : undefined,
                                    paintOrder: "stroke fill",
                                    textShadow,
                                }}
                            >
                                {ov.animation === "one_word" ? (
                                    (() => {
                                        const cur =
                                            visibleWords[Math.max(0, activeWordIdx)] ||
                                            visibleWords[0];
                                        return (
                                            <span
                                                style={{
                                                    color: ov.highlight_color || ov.primary_color,
                                                    transform: "scale(1.12)",
                                                }}
                                                className="inline-block transition-transform duration-75"
                                            >
                                                {cur.word}
                                                {activeBlock.emoji ? ` ${activeBlock.emoji}` : ""}
                                            </span>
                                        );
                                    })()
                                ) : (
                                    <>
                                        {visibleWords.map((w, idx) => {
                                            if (
                                                ov.animation === "word_by_word" &&
                                                activeWordIdx >= 0 &&
                                                idx > activeWordIdx
                                            ) {
                                                return null;
                                            }
                                            const isActive = idx === activeWordIdx;
                                            const isPast = activeWordIdx >= 0 && idx < activeWordIdx;

                                            let color = ov.primary_color;
                                            let transform = "scale(1)";
                                            let bg = "transparent";
                                            let filter = "none";

                                            if (ov.animation === "sweep") {
                                                color =
                                                    isActive || isPast
                                                        ? ov.highlight_color
                                                        : ov.primary_color;
                                            } else if (isActive) {
                                                color = ov.highlight_color || ov.primary_color;
                                                if (ov.animation === "stamp") {
                                                    transform = "scale(1.18)";
                                                } else if (ov.animation === "jelly") {
                                                    transform = "scale(1.14) translateY(-2px)";
                                                } else if (ov.animation === "chase") {
                                                    bg = ov.highlight_color;
                                                    color = "#FFFFFF";
                                                    transform = "scale(1.06)";
                                                } else if (ov.animation === "glow") {
                                                    filter = `drop-shadow(0 0 10px ${ov.highlight_color})`;
                                                    transform = "scale(1.08)";
                                                } else if (ov.animation === "highlight") {
                                                    transform = "scale(1.07)";
                                                }
                                            }

                                            return (
                                                <span
                                                    key={w.id}
                                                    style={{
                                                        color,
                                                        transform,
                                                        backgroundColor: bg,
                                                        filter,
                                                    }}
                                                    className="inline-block rounded px-0.5 transition-all duration-75"
                                                >
                                                    {w.word}
                                                </span>
                                            );
                                        })}
                                        {activeBlock.emoji && (
                                            <span className="inline-block ml-0.5">
                                                {activeBlock.emoji}
                                            </span>
                                        )}
                                    </>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Transport Control Bar */}
            <div className="h-12 flex-shrink-0 border-t border-white/10 bg-[#08080c] px-4 flex items-center justify-between gap-4">
                <div className="font-mono text-xs text-slate-300 tabular-nums w-36">
                    <span className="text-white font-bold">{formatTimecode(currentTime)}</span>
                    <span className="text-slate-600 mx-1">/</span>
                    <span className="text-slate-500">{formatTimecode(duration)}</span>
                </div>

                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        onClick={() => stepFrame(-1)}
                        title="Previous frame (←)"
                        className="h-8 w-8 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-slate-300"
                    >
                        <SkipBack className="h-3.5 w-3.5" />
                    </button>
                    <button
                        type="button"
                        onClick={() => onPlayStateChange(!isPlaying)}
                        title="Play / Pause (Space)"
                        className="h-9 w-9 rounded-full bg-primary hover:bg-primary/90 flex items-center justify-center text-white shadow-lg"
                    >
                        {isPlaying ? (
                            <Pause className="h-4 w-4" />
                        ) : (
                            <Play className="h-4 w-4 ml-0.5" />
                        )}
                    </button>
                    <button
                        type="button"
                        onClick={() => stepFrame(1)}
                        title="Next frame (→)"
                        className="h-8 w-8 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-slate-300"
                    >
                        <SkipForward className="h-3.5 w-3.5" />
                    </button>
                </div>

                <div className="flex items-center gap-2 w-36 justify-end">
                    <select
                        value={playbackRate}
                        onChange={(e) => onPlaybackRateChange(Number(e.target.value))}
                        className="rounded-lg bg-white/5 border border-white/10 px-2 py-1 text-xs font-bold text-slate-200 focus:outline-none focus:border-primary"
                    >
                        {SPEEDS.map((s) => (
                            <option key={s} value={s} className="bg-slate-900">
                                {s}×
                            </option>
                        ))}
                    </select>
                    <button
                        type="button"
                        onClick={() => setMuted((m) => !m)}
                        className="h-8 w-8 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-slate-300"
                    >
                        {muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
                    </button>
                </div>
            </div>
        </section>
    );
}
