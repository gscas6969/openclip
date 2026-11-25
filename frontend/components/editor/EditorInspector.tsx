"use client";

import { Trash2, Scissors, Sparkles, Type, Image as ImageIcon, Film } from "lucide-react";
import {
    CaptionAnimation,
    CaptionOverrides,
    Clip,
    ClipEditState,
    LogoConfig,
} from "@/lib/types";

const FONTS = [
    "Arial Black",
    "Impact",
    "Arial Bold",
    "Arial",
    "Helvetica",
    "Georgia",
    "Space Grotesk",
    "Verdana",
];

const ANIMATIONS: { value: CaptionAnimation; label: string }[] = [
    { value: "jelly", label: "Jelly Bounce" },
    { value: "stamp", label: "Stamp Slam" },
    { value: "chase", label: "Chase Pill" },
    { value: "sweep", label: "Sweep Karaoke" },
    { value: "glow", label: "Neon Glow" },
    { value: "highlight", label: "Word Highlight" },
    { value: "word_by_word", label: "Word-by-Word Build" },
    { value: "one_word", label: "One Word Punch" },
];

interface EditorInspectorProps {
    clip: Clip;
    editState: ClipEditState;
    duration: number;
    selectedTrack: "captions" | "logo" | "video";
    selectedBlockId: string | null;
    onSelectTrack: (track: "captions" | "logo" | "video") => void;
    onUpdateOverrides: (patch: Partial<CaptionOverrides>) => void;
    onToggleCaptions: (enabled: boolean) => void;
    onUpdateLogo: (patch: Partial<LogoConfig>) => void;
    onUpdateTrim: (trimStart: number, trimEnd: number) => void;
    onRemoveCut: (cutId: string) => void;
    onSeek: (time: number) => void;
    onUpdateBlock: (blockId: string, patch: { text?: string; emoji?: string; start?: number; end?: number }) => void;
    onDeleteBlock: (blockId: string) => void;
}

export function EditorInspector({
    clip,
    editState,
    duration,
    selectedTrack,
    selectedBlockId,
    onSelectTrack,
    onUpdateOverrides,
    onToggleCaptions,
    onUpdateLogo,
    onUpdateTrim,
    onRemoveCut,
    onSeek,
    onUpdateBlock,
    onDeleteBlock,
}: EditorInspectorProps) {
    const ov = editState.caption_overrides;
    const logo = editState.logo;
    const selectedBlock = editState.caption_blocks.find((b) => b.id === selectedBlockId);

    return (
        <aside className="w-[300px] flex-shrink-0 border-l border-white/10 bg-[#08080c] flex flex-col overflow-hidden">
            {/* Inspector Track Tabs */}
            <div className="p-2.5 border-b border-white/10 grid grid-cols-3 gap-1 bg-[#050508]">
                {(
                    [
                        { id: "captions", label: "Captions", icon: <Type className="h-3.5 w-3.5" /> },
                        { id: "logo", label: "Logo", icon: <ImageIcon className="h-3.5 w-3.5" /> },
                        { id: "video", label: "Clip", icon: <Film className="h-3.5 w-3.5" /> },
                    ] as const
                ).map((t) => (
                    <button
                        key={t.id}
                        type="button"
                        onClick={() => onSelectTrack(t.id)}
                        className={`py-1.5 rounded-lg text-[11px] font-bold flex items-center justify-center gap-1.5 transition-colors ${
                            selectedTrack === t.id
                                ? "bg-primary text-white"
                                : "text-slate-400 hover:text-white hover:bg-white/5"
                        }`}
                    >
                        {t.icon}
                        {t.label}
                    </button>
                ))}
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-5">
                {/* CAPTIONS INSPECTOR */}
                {selectedTrack === "captions" && (
                    <>
                        <div className="flex items-center justify-between">
                            <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                                Captions Layer
                            </span>
                            <label className="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={editState.caption_enabled}
                                    onChange={(e) => onToggleCaptions(e.target.checked)}
                                    className="accent-primary"
                                />
                                Enabled
                            </label>
                        </div>

                        {/* Motion Animation */}
                        <div className="space-y-1.5">
                            <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                                Kinetic Motion
                            </label>
                            <select
                                value={ov.animation}
                                onChange={(e) =>
                                    onUpdateOverrides({ animation: e.target.value as CaptionAnimation })
                                }
                                className="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-xs text-white focus:border-primary focus:outline-none"
                            >
                                {ANIMATIONS.map((a) => (
                                    <option key={a.value} value={a.value} className="bg-slate-900">
                                        {a.label}
                                    </option>
                                ))}
                            </select>
                        </div>

                        {/* Font & Case */}
                        <div className="grid grid-cols-3 gap-2 items-end">
                            <div className="col-span-2 space-y-1.5">
                                <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                                    Font
                                </label>
                                <select
                                    value={ov.font}
                                    onChange={(e) => onUpdateOverrides({ font: e.target.value })}
                                    className="w-full rounded-lg bg-white/5 border border-white/10 px-2.5 py-2 text-xs text-white focus:border-primary focus:outline-none"
                                >
                                    {FONTS.map((f) => (
                                        <option key={f} value={f} className="bg-slate-900">
                                            {f}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <button
                                type="button"
                                onClick={() => onUpdateOverrides({ uppercase: !ov.uppercase })}
                                className={`h-[34px] rounded-lg border text-xs font-black transition-colors ${
                                    ov.uppercase
                                        ? "border-primary bg-primary/20 text-indigo-300"
                                        : "border-white/10 bg-white/5 text-slate-400"
                                }`}
                                title="Toggle UPPERCASE"
                            >
                                AA
                            </button>
                        </div>

                        {/* Sliders: Size, Position Y, Outline, Shadow */}
                        <div className="space-y-3">
                            <div>
                                <div className="flex justify-between text-[11px] mb-1">
                                    <span className="text-slate-400 font-semibold">Font Size</span>
                                    <span className="font-mono text-white">{ov.fontsize}px</span>
                                </div>
                                <input
                                    type="range"
                                    min={44}
                                    max={140}
                                    value={ov.fontsize}
                                    onChange={(e) =>
                                        onUpdateOverrides({ fontsize: Number(e.target.value) })
                                    }
                                    className="w-full accent-primary"
                                />
                            </div>

                            <div>
                                <div className="flex justify-between text-[11px] mb-1">
                                    <span className="text-slate-400 font-semibold">Vertical Position</span>
                                    <span className="font-mono text-white">{ov.position_y}%</span>
                                </div>
                                <input
                                    type="range"
                                    min={12}
                                    max={90}
                                    value={ov.position_y}
                                    onChange={(e) =>
                                        onUpdateOverrides({ position_y: Number(e.target.value) })
                                    }
                                    className="w-full accent-primary"
                                />
                            </div>

                            <div>
                                <div className="flex justify-between text-[11px] mb-1">
                                    <span className="text-slate-400 font-semibold">Highlight Sync Offset</span>
                                    <span className="font-mono text-white">
                                        {(ov.sync_offset_ms ?? -120) >= 0 ? "+" : ""}
                                        {ov.sync_offset_ms ?? -120}ms
                                    </span>
                                </div>
                                <input
                                    type="range"
                                    min={-600}
                                    max={600}
                                    step={20}
                                    value={ov.sync_offset_ms ?? -120}
                                    onChange={(e) =>
                                        onUpdateOverrides({ sync_offset_ms: Number(e.target.value) })
                                    }
                                    className="w-full accent-primary"
                                />
                                <p className="text-[10px] text-slate-500 mt-0.5">
                                    Negative = earlier highlight · Positive = later highlight
                                </p>
                            </div>

                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <div className="flex justify-between text-[11px] mb-1">
                                        <span className="text-slate-400">Stroke</span>
                                        <span className="font-mono text-white">{ov.outline_width}</span>
                                    </div>
                                    <input
                                        type="range"
                                        min={0}
                                        max={12}
                                        value={ov.outline_width}
                                        onChange={(e) =>
                                            onUpdateOverrides({ outline_width: Number(e.target.value) })
                                        }
                                        className="w-full accent-primary"
                                    />
                                </div>
                                <div>
                                    <div className="flex justify-between text-[11px] mb-1">
                                        <span className="text-slate-400">Shadow</span>
                                        <span className="font-mono text-white">{ov.shadow}</span>
                                    </div>
                                    <input
                                        type="range"
                                        min={0}
                                        max={8}
                                        value={ov.shadow}
                                        onChange={(e) =>
                                            onUpdateOverrides({ shadow: Number(e.target.value) })
                                        }
                                        className="w-full accent-primary"
                                    />
                                </div>
                            </div>
                        </div>

                        {/* Colors */}
                        <div className="space-y-2.5 pt-3 border-t border-white/10">
                            <span className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                                Colors &amp; Box
                            </span>
                            <div className="grid grid-cols-3 gap-2">
                                <label className="flex flex-col gap-1 text-[10px] text-slate-400">
                                    Text
                                    <input
                                        type="color"
                                        value={ov.primary_color.slice(0, 7)}
                                        onChange={(e) =>
                                            onUpdateOverrides({ primary_color: e.target.value })
                                        }
                                        className="h-8 w-full rounded bg-transparent border border-white/10 cursor-pointer"
                                    />
                                </label>
                                <label className="flex flex-col gap-1 text-[10px] text-slate-400">
                                    Highlight
                                    <input
                                        type="color"
                                        value={(ov.highlight_color || "#FFE600").slice(0, 7)}
                                        onChange={(e) =>
                                            onUpdateOverrides({ highlight_color: e.target.value })
                                        }
                                        className="h-8 w-full rounded bg-transparent border border-white/10 cursor-pointer"
                                    />
                                </label>
                                <label className="flex flex-col gap-1 text-[10px] text-slate-400">
                                    Outline
                                    <input
                                        type="color"
                                        value={ov.outline_color.slice(0, 7)}
                                        onChange={(e) =>
                                            onUpdateOverrides({ outline_color: e.target.value })
                                        }
                                        className="h-8 w-full rounded bg-transparent border border-white/10 cursor-pointer"
                                    />
                                </label>
                            </div>
                            <div className="flex items-center justify-between pt-1">
                                <label className="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={ov.background}
                                        onChange={(e) =>
                                            onUpdateOverrides({ background: e.target.checked })
                                        }
                                        className="accent-primary"
                                    />
                                    Background Box
                                </label>
                                {ov.background && (
                                    <input
                                        type="color"
                                        value={(ov.bg_color || "#000000").slice(0, 7)}
                                        onChange={(e) =>
                                            onUpdateOverrides({ bg_color: `${e.target.value}CC` })
                                        }
                                        className="h-6 w-10 rounded bg-transparent border border-white/10 cursor-pointer"
                                    />
                                )}
                            </div>
                        </div>

                        {/* Selected Block Timing / Text */}
                        {selectedBlock && (
                            <div className="space-y-2.5 pt-3 border-t border-white/10">
                                <div className="flex items-center justify-between">
                                    <span className="text-[11px] font-bold text-indigo-300 uppercase tracking-wider">
                                        Selected Caption Block
                                    </span>
                                    <button
                                        type="button"
                                        onClick={() => onDeleteBlock(selectedBlock.id)}
                                        className="text-slate-500 hover:text-red-400"
                                        title="Delete caption block"
                                    >
                                        <Trash2 className="h-3.5 w-3.5" />
                                    </button>
                                </div>
                                <textarea
                                    rows={2}
                                    value={selectedBlock.text}
                                    onChange={(e) =>
                                        onUpdateBlock(selectedBlock.id, { text: e.target.value })
                                    }
                                    className="w-full rounded-lg bg-white/5 border border-white/10 p-2 text-xs text-white focus:border-primary focus:outline-none"
                                />
                                <div className="grid grid-cols-2 gap-2">
                                    <label className="text-[10px] text-slate-400">
                                        Start (s)
                                        <input
                                            type="number"
                                            step={0.05}
                                            value={Number(selectedBlock.start.toFixed(2))}
                                            onChange={(e) =>
                                                onUpdateBlock(selectedBlock.id, {
                                                    start: Math.max(0, Number(e.target.value)),
                                                })
                                            }
                                            className="mt-1 w-full rounded bg-white/5 border border-white/10 px-2 py-1 text-xs font-mono text-white"
                                        />
                                    </label>
                                    <label className="text-[10px] text-slate-400">
                                        End (s)
                                        <input
                                            type="number"
                                            step={0.05}
                                            value={Number(selectedBlock.end.toFixed(2))}
                                            onChange={(e) =>
                                                onUpdateBlock(selectedBlock.id, {
                                                    end: Math.max(
                                                        selectedBlock.start + 0.1,
                                                        Number(e.target.value),
                                                    ),
                                                })
                                            }
                                            className="mt-1 w-full rounded bg-white/5 border border-white/10 px-2 py-1 text-xs font-mono text-white"
                                        />
                                    </label>
                                </div>
                            </div>
                        )}
                    </>
                )}

                {/* LOGO INSPECTOR */}
                {selectedTrack === "logo" && (
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                                Logo Transform
                            </span>
                            <label className="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={logo.enabled}
                                    onChange={(e) => onUpdateLogo({ enabled: e.target.checked })}
                                    className="accent-primary"
                                />
                                Visible
                            </label>
                        </div>

                        <div className="grid grid-cols-4 gap-1.5">
                            {[
                                { label: "TL", x: 16, y: 8 },
                                { label: "TR", x: 84, y: 8 },
                                { label: "BL", x: 16, y: 88 },
                                { label: "BR", x: 84, y: 88 },
                            ].map((pos) => (
                                <button
                                    key={pos.label}
                                    type="button"
                                    onClick={() => onUpdateLogo({ x: pos.x, y: pos.y })}
                                    className="py-1.5 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-[11px] font-bold text-slate-300"
                                >
                                    {pos.label}
                                </button>
                            ))}
                        </div>

                        {[
                            { label: "Horizontal (X)", key: "x" as const, min: 5, max: 95, val: logo.x, unit: "%" },
                            { label: "Vertical (Y)", key: "y" as const, min: 5, max: 95, val: logo.y, unit: "%" },
                            { label: "Scale Width", key: "scale" as const, min: 6, max: 50, val: logo.scale, unit: "%" },
                            { label: "Opacity", key: "opacity" as const, min: 10, max: 100, val: logo.opacity, unit: "%" },
                        ].map((s) => (
                            <div key={s.key}>
                                <div className="flex justify-between text-[11px] mb-1">
                                    <span className="text-slate-400 font-semibold">{s.label}</span>
                                    <span className="font-mono text-white">
                                        {s.val}
                                        {s.unit}
                                    </span>
                                </div>
                                <input
                                    type="range"
                                    min={s.min}
                                    max={s.max}
                                    value={s.val}
                                    onChange={(e) =>
                                        onUpdateLogo({ [s.key]: Number(e.target.value) })
                                    }
                                    className="w-full accent-primary"
                                />
                            </div>
                        ))}
                    </div>
                )}

                {/* CLIP / VIDEO INSPECTOR */}
                {selectedTrack === "video" && (
                    <div className="space-y-4">
                        <span className="block text-xs font-bold uppercase tracking-wider text-slate-300">
                            Trim &amp; Cuts
                        </span>
                        <div className="grid grid-cols-2 gap-2">
                            <label className="text-[10px] text-slate-400">
                                Trim In (s)
                                <input
                                    type="number"
                                    step={0.1}
                                    min={0}
                                    max={Math.max(0, editState.trim_end - 0.5)}
                                    value={Number(editState.trim_start.toFixed(2))}
                                    onChange={(e) =>
                                        onUpdateTrim(
                                            Math.max(0, Number(e.target.value)),
                                            editState.trim_end,
                                        )
                                    }
                                    className="mt-1 w-full rounded-lg bg-white/5 border border-white/10 px-2.5 py-1.5 text-xs font-mono text-white"
                                />
                            </label>
                            <label className="text-[10px] text-slate-400">
                                Trim Out (s)
                                <input
                                    type="number"
                                    step={0.1}
                                    min={editState.trim_start + 0.5}
                                    max={duration || 999}
                                    value={Number(editState.trim_end.toFixed(2))}
                                    onChange={(e) =>
                                        onUpdateTrim(
                                            editState.trim_start,
                                            Math.min(duration || 999, Number(e.target.value)),
                                        )
                                    }
                                    className="mt-1 w-full rounded-lg bg-white/5 border border-white/10 px-2.5 py-1.5 text-xs font-mono text-white"
                                />
                            </label>
                        </div>

                        {/* Active Cuts List */}
                        <div className="space-y-2 pt-2 border-t border-white/10">
                            <div className="flex items-center justify-between">
                                <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                                    <Scissors className="h-3 w-3" />
                                    Removed Gaps ({editState.cuts.length})
                                </span>
                            </div>
                            {editState.cuts.length === 0 ? (
                                <p className="text-[11px] text-slate-500">
                                    No cuts yet. Use &ldquo;Cut Silences&rdquo; in Words or add a cut on the timeline.
                                </p>
                            ) : (
                                <div className="space-y-1.5">
                                    {editState.cuts.map((c) => (
                                        <div
                                            key={c.id}
                                            className="flex items-center justify-between rounded-lg bg-red-500/10 border border-red-500/20 px-2.5 py-1.5 text-xs"
                                        >
                                            <button
                                                type="button"
                                                onClick={() => onSeek(c.start)}
                                                className="font-mono text-red-300 hover:underline text-[11px]"
                                            >
                                                {c.start.toFixed(2)}s – {c.end.toFixed(2)}s (
                                                {c.reason || "cut"})
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => onRemoveCut(c.id)}
                                                className="text-red-400 hover:text-white"
                                                title="Restore segment"
                                            >
                                                <Trash2 className="h-3 w-3" />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* AI Clip Insights */}
                        <div className="space-y-2 pt-3 border-t border-white/10">
                            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                                <Sparkles className="h-3 w-3 text-primary" />
                                AI Insights
                            </span>
                            {clip.viral_score != null && (
                                <div className="rounded-lg bg-white/5 p-2.5 flex items-center justify-between">
                                    <span className="text-xs text-slate-400">Viral Score</span>
                                    <span className="text-sm font-black text-indigo-300">
                                        {clip.viral_score}/10
                                    </span>
                                </div>
                            )}
                            {clip.reason && (
                                <p className="text-xs text-slate-400 leading-relaxed bg-white/[0.03] rounded-lg p-2.5 border border-white/5">
                                    {clip.reason}
                                </p>
                            )}
                            {clip.hashtags && clip.hashtags.length > 0 && (
                                <div className="flex flex-wrap gap-1">
                                    {clip.hashtags.map((h, i) => (
                                        <span
                                            key={i}
                                            className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-primary/15 text-indigo-300"
                                        >
                                            {h}
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </aside>
    );
}
