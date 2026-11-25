"use client";

import { useState, useRef } from "react";
import {
    Type,
    FileText,
    Smile,
    Image as ImageIcon,
    LayoutTemplate,
    Wand2,
    Trash2,
    RotateCcw,
    Upload,
    Plus,
    Loader2,
    Scissors,
    Sparkles,
} from "lucide-react";
import {
    BrandKit,
    CaptionBlock,
    CaptionStyle,
    ClipEditState,
} from "@/lib/types";
import { getApiBaseUrl } from "@/lib/api";

export type LeftRailTab = "presets" | "words" | "emojis" | "brand" | "layout";

const EMOJI_PALETTE = [
    "🔥", "🤯", "💰", "🚀", "⚡", "💯", "🎯", "👀",
    "😂", "❤️", "👑", "🧠", "💡", "🛑", "❌", "⚠️",
    "📈", "🏆", "🎙️", "🎬", "🤖", "💻", "🤫", "💎",
    "⏰", "🤝", "🌍", "✨", "🎉", "💀", "👏", "🗣️",
];

const LAYOUT_OPTIONS = [
    {
        key: "dynamic_zoom",
        name: "Smart Face Track",
        desc: "Smooth MediaPipe dynamic zoom tracking the main speaker.",
    },
    {
        key: "podcast_split",
        name: "Podcast Split-Screen",
        desc: "2-speaker stacked vertical split (Speaker A top, Speaker B bottom).",
    },
    {
        key: "podcast_switch",
        name: "Podcast Active Speaker",
        desc: "Automatically cuts to whoever is talking via audio + lip tracking.",
    },
    {
        key: "tutorial",
        name: "Tutorial (Screen + Face)",
        desc: "Top canvas for screen/slides + bottom speaker facecam.",
    },
    {
        key: "original",
        name: "Original + Blur Fill",
        desc: "Full uncropped 16:9 frame centered over a blurred 9:16 backdrop.",
    },
];

interface EditorLeftRailProps {
    activeTab: LeftRailTab;
    onTabChange: (tab: LeftRailTab) => void;
    editState: ClipEditState;
    styles: CaptionStyle[];
    brandKits: BrandKit[];
    selectedBlockId: string | null;
    currentTime: number;
    onApplyPreset: (style: CaptionStyle) => void;
    onSeek: (time: number) => void;
    onSelectBlock: (blockId: string) => void;
    onToggleWordDeleted: (blockId: string, wordId: string) => void;
    onUpdateWordText: (blockId: string, wordId: string, text: string) => void;
    onRemoveFillers: () => void;
    onDetectSilences: () => void;
    detectingFillers: boolean;
    onResyncWhisper: () => void;
    resyncingWhisper: boolean;
    onAutoEmojis: () => void;
    injectingEmojis: boolean;
    onSetBlockEmoji: (blockId: string, emoji: string) => void;
    onUploadLogo: (file: File) => void;
    uploadingLogo: boolean;
    onToggleLogo: (enabled: boolean) => void;
    onSaveBrandKit: (name: string) => void;
    onApplyBrandKit: (kit: BrandKit) => void;
    onDeleteBrandKit: (kitId: string) => void;
    onRelayout: (layoutMode: string) => void;
    relayouting: boolean;
}

export function EditorLeftRail({
    activeTab,
    onTabChange,
    editState,
    styles,
    brandKits,
    selectedBlockId,
    currentTime,
    onApplyPreset,
    onSeek,
    onSelectBlock,
    onToggleWordDeleted,
    onUpdateWordText,
    onRemoveFillers,
    onDetectSilences,
    detectingFillers,
    onResyncWhisper,
    resyncingWhisper,
    onAutoEmojis,
    injectingEmojis,
    onSetBlockEmoji,
    onUploadLogo,
    uploadingLogo,
    onToggleLogo,
    onSaveBrandKit,
    onApplyBrandKit,
    onDeleteBrandKit,
    onRelayout,
    relayouting,
}: EditorLeftRailProps) {
    const [presetFilter, setPresetFilter] = useState<"all" | "kinetic" | "clean" | "vibrant">("all");
    const [editingWordKey, setEditingWordKey] = useState<string | null>(null);
    const [editingWordVal, setEditingWordVal] = useState("");
    const [newKitName, setNewKitName] = useState("");
    const logoInputRef = useRef<HTMLInputElement>(null);

    const tabs: { id: LeftRailTab; label: string; icon: React.ReactNode }[] = [
        { id: "presets", label: "Presets", icon: <Type className="h-4 w-4" /> },
        { id: "words", label: "Words", icon: <FileText className="h-4 w-4" /> },
        { id: "emojis", label: "Emojis", icon: <Smile className="h-4 w-4" /> },
        { id: "brand", label: "Brand", icon: <ImageIcon className="h-4 w-4" /> },
        { id: "layout", label: "Layout", icon: <LayoutTemplate className="h-4 w-4" /> },
    ];

    const filteredStyles = styles.filter((s) => {
        if (s.key === "none") return true;
        if (presetFilter === "all") return true;
        return s.category === presetFilter;
    });

    const selectedBlock: CaptionBlock | undefined =
        editState.caption_blocks.find((b) => b.id === selectedBlockId) ||
        editState.caption_blocks.find((b) => currentTime >= b.start && currentTime <= b.end) ||
        editState.caption_blocks[0];

    const fillerCount = editState.caption_blocks.reduce(
        (acc, b) => acc + b.words.filter((w) => w.is_filler && !w.deleted).length,
        0,
    );

    const deletedCount = editState.caption_blocks.reduce(
        (acc, b) => acc + b.words.filter((w) => w.deleted).length,
        0,
    );

    const baseUrl = getApiBaseUrl();

    return (
        <aside className="w-[330px] flex-shrink-0 border-r border-white/10 bg-[#08080c] flex overflow-hidden">
            {/* Vertical Icon Rail */}
            <div className="w-16 flex-shrink-0 border-r border-white/5 flex flex-col items-center py-3 gap-1.5 bg-[#050508]">
                {tabs.map((tab) => (
                    <button
                        key={tab.id}
                        type="button"
                        onClick={() => onTabChange(tab.id)}
                        className={`w-12 py-2.5 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${
                            activeTab === tab.id
                                ? "bg-primary/20 text-indigo-300 border border-primary/40"
                                : "text-slate-500 hover:text-slate-200 hover:bg-white/5 border border-transparent"
                        }`}
                    >
                        {tab.icon}
                        <span className="text-[10px] font-semibold">{tab.label}</span>
                    </button>
                ))}
            </div>

            {/* Active Drawer Panel */}
            <div className="flex-1 flex flex-col overflow-hidden">
                {/* PRESETS TAB */}
                {activeTab === "presets" && (
                    <div className="flex-1 flex flex-col overflow-hidden">
                        <div className="p-3.5 border-b border-white/5 space-y-2.5">
                            <div className="flex items-center justify-between">
                                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
                                    Caption Presets ({styles.length - 1})
                                </h3>
                            </div>
                            <div className="grid grid-cols-4 gap-1 bg-white/5 p-1 rounded-lg">
                                {(["all", "kinetic", "clean", "vibrant"] as const).map((cat) => (
                                    <button
                                        key={cat}
                                        type="button"
                                        onClick={() => setPresetFilter(cat)}
                                        className={`py-1 rounded-md text-[10px] font-bold capitalize transition-colors ${
                                            presetFilter === cat
                                                ? "bg-primary text-white"
                                                : "text-slate-400 hover:text-white"
                                        }`}
                                    >
                                        {cat}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div className="flex-1 overflow-y-auto custom-scrollbar p-3 grid grid-cols-2 gap-2.5 content-start">
                            {filteredStyles.map((style) => {
                                const isActive =
                                    style.key === "none"
                                        ? !editState.caption_enabled
                                        : editState.caption_enabled && editState.caption_style === style.key;
                                return (
                                    <button
                                        key={style.key}
                                        type="button"
                                        onClick={() => onApplyPreset(style)}
                                        className={`group rounded-xl p-2 text-left border transition-all flex flex-col gap-1.5 ${
                                            isActive
                                                ? "border-primary bg-primary/15"
                                                : "border-white/10 bg-white/[0.02] hover:border-white/25 hover:bg-white/[0.05]"
                                        }`}
                                    >
                                        <div
                                            className="h-14 w-full rounded-lg flex items-center justify-center overflow-hidden px-1.5 relative bg-[#0c0c12]"
                                            style={{
                                                backgroundColor:
                                                    style.preview_colors.background || "#0c0c12",
                                            }}
                                        >
                                            {style.key === "none" ? (
                                                <span className="text-xs font-bold text-slate-500 line-through">
                                                    OFF
                                                </span>
                                            ) : (
                                                <span
                                                    className="text-xs font-black tracking-tight text-center leading-tight"
                                                    style={{
                                                        color: style.preview_colors.text || "#fff",
                                                        textTransform: style.config?.uppercase
                                                            ? "uppercase"
                                                            : "none",
                                                        textShadow: "0 2px 4px rgba(0,0,0,0.85)",
                                                    }}
                                                >
                                                    THE{" "}
                                                    <span
                                                        style={{
                                                            color:
                                                                style.preview_colors.highlight ||
                                                                style.preview_colors.text ||
                                                                "#FFE600",
                                                        }}
                                                    >
                                                        HOOK
                                                    </span>
                                                </span>
                                            )}
                                            {style.key !== "none" && (
                                                <span className="absolute bottom-1 right-1 text-[8px] font-bold uppercase px-1 py-0.5 rounded bg-black/70 text-slate-300">
                                                    {style.animation}
                                                </span>
                                            )}
                                        </div>
                                        <span className="text-[11px] font-bold text-slate-200 truncate">
                                            {style.name}
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* WORDS / TRANSCRIPT EDITOR TAB */}
                {activeTab === "words" && (
                    <div className="flex-1 flex flex-col overflow-hidden">
                        <div className="p-3.5 border-b border-white/5 space-y-2.5">
                            <div className="flex items-center justify-between">
                                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
                                    Transcript &amp; Cuts
                                </h3>
                                <span className="text-[10px] text-slate-500">
                                    {deletedCount} cut · {editState.cuts.length} gaps
                                </span>
                            </div>
                            <div className="grid grid-cols-2 gap-1.5">
                                <button
                                    type="button"
                                    onClick={onRemoveFillers}
                                    className="px-2.5 py-2 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 text-[11px] font-bold text-amber-300 flex items-center justify-center gap-1.5 transition-colors"
                                >
                                    <Scissors className="h-3.5 w-3.5" />
                                    Fillers ({fillerCount})
                                </button>
                                <button
                                    type="button"
                                    onClick={onDetectSilences}
                                    disabled={detectingFillers}
                                    className="px-2.5 py-2 rounded-lg bg-primary/15 hover:bg-primary/25 border border-primary/30 text-[11px] font-bold text-indigo-300 flex items-center justify-center gap-1.5 transition-colors disabled:opacity-50"
                                >
                                    {detectingFillers ? (
                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    ) : (
                                        <Wand2 className="h-3.5 w-3.5" />
                                    )}
                                    Cut Silences
                                </button>
                            </div>
                            <button
                                type="button"
                                onClick={onResyncWhisper}
                                disabled={resyncingWhisper}
                                className="w-full py-2 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-[11px] font-bold text-emerald-300 flex items-center justify-center gap-1.5 transition-colors disabled:opacity-50"
                                title="Transcribe this clip's exact audio with Whisper for millisecond-accurate word timing"
                            >
                                {resyncingWhisper ? (
                                    <>
                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                        Syncing audio with Whisper…
                                    </>
                                ) : (
                                    <>
                                        <Sparkles className="h-3.5 w-3.5" />
                                        Re-Sync Captions to Exact Audio (Whisper)
                                    </>
                                )}
                            </button>
                            <p className="text-[10px] text-slate-500 leading-relaxed">
                                Click a word to seek · Double-click to fix typos · Click ✕ to cut word from video.
                            </p>
                        </div>

                        <div className="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-2.5">
                            {editState.caption_blocks.length === 0 ? (
                                <p className="text-xs text-slate-500 text-center py-8">
                                    No transcript blocks available for this clip.
                                </p>
                            ) : (
                                editState.caption_blocks.map((blk) => {
                                    const isCurrent =
                                        currentTime >= blk.start && currentTime <= blk.end;
                                    const isSelected = selectedBlockId === blk.id;
                                    return (
                                        <div
                                            key={blk.id}
                                            onClick={() => {
                                                onSelectBlock(blk.id);
                                                onSeek(blk.start);
                                            }}
                                            className={`rounded-xl p-2.5 border transition-colors cursor-pointer ${
                                                isSelected
                                                    ? "border-primary/60 bg-primary/10"
                                                    : isCurrent
                                                      ? "border-white/20 bg-white/[0.06]"
                                                      : "border-white/5 bg-white/[0.02] hover:border-white/15"
                                            }`}
                                        >
                                            <div className="flex items-center justify-between mb-1.5">
                                                <span className="text-[10px] font-mono text-slate-500">
                                                    {blk.start.toFixed(2)}s – {blk.end.toFixed(2)}s
                                                </span>
                                                {blk.emoji && (
                                                    <span className="text-xs">{blk.emoji}</span>
                                                )}
                                            </div>
                                            <div className="flex flex-wrap gap-1">
                                                {blk.words.map((w) => {
                                                    const wKey = `${blk.id}:${w.id}`;
                                                    const isWordActive =
                                                        !w.deleted &&
                                                        currentTime >= w.start &&
                                                        currentTime <= w.end;

                                                    if (editingWordKey === wKey) {
                                                        return (
                                                            <input
                                                                key={w.id}
                                                                autoFocus
                                                                value={editingWordVal}
                                                                onChange={(e) =>
                                                                    setEditingWordVal(e.target.value)
                                                                }
                                                                onBlur={() => {
                                                                    if (editingWordVal.trim()) {
                                                                        onUpdateWordText(
                                                                            blk.id,
                                                                            w.id,
                                                                            editingWordVal.trim(),
                                                                        );
                                                                    }
                                                                    setEditingWordKey(null);
                                                                }}
                                                                onKeyDown={(e) => {
                                                                    if (e.key === "Enter") {
                                                                        (
                                                                            e.target as HTMLInputElement
                                                                        ).blur();
                                                                    } else if (e.key === "Escape") {
                                                                        setEditingWordKey(null);
                                                                    }
                                                                }}
                                                                onClick={(e) => e.stopPropagation()}
                                                                className="w-20 px-1.5 py-0.5 text-xs bg-black border border-primary rounded text-white focus:outline-none"
                                                            />
                                                        );
                                                    }

                                                    return (
                                                        <span
                                                            key={w.id}
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                onSelectBlock(blk.id);
                                                                onSeek(w.start);
                                                            }}
                                                            onDoubleClick={(e) => {
                                                                e.stopPropagation();
                                                                setEditingWordKey(wKey);
                                                                setEditingWordVal(w.word);
                                                            }}
                                                            className={`group/w inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs transition-colors ${
                                                                w.deleted
                                                                    ? "bg-red-500/15 text-red-400/70 line-through"
                                                                    : isWordActive
                                                                      ? "bg-primary text-white font-bold"
                                                                      : w.is_filler
                                                                        ? "bg-amber-500/15 text-amber-300 border border-amber-500/30"
                                                                        : "bg-white/5 text-slate-200 hover:bg-white/15"
                                                            }`}
                                                        >
                                                            {w.word}
                                                            <button
                                                                type="button"
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    onToggleWordDeleted(blk.id, w.id);
                                                                }}
                                                                title={
                                                                    w.deleted
                                                                        ? "Restore word"
                                                                        : "Cut word from clip"
                                                                }
                                                                className="opacity-40 group-hover/w:opacity-100 hover:text-red-300"
                                                            >
                                                                {w.deleted ? (
                                                                    <RotateCcw className="h-2.5 w-2.5" />
                                                                ) : (
                                                                    <span className="text-[10px]">
                                                                        ×
                                                                    </span>
                                                                )}
                                                            </button>
                                                        </span>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    );
                                })
                            )}
                        </div>
                    </div>
                )}

                {/* EMOJIS TAB */}
                {activeTab === "emojis" && (
                    <div className="flex-1 flex flex-col overflow-hidden">
                        <div className="p-3.5 border-b border-white/5 space-y-2.5">
                            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
                                Contextual Emojis
                            </h3>
                            <button
                                type="button"
                                onClick={onAutoEmojis}
                                disabled={injectingEmojis}
                                className="w-full py-2.5 rounded-xl bg-primary hover:bg-primary/90 text-xs font-bold text-white flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
                            >
                                {injectingEmojis ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Sparkles className="h-3.5 w-3.5" />
                                )}
                                Auto-Inject Smart Emojis
                            </button>
                        </div>

                        <div className="flex-1 overflow-y-auto custom-scrollbar p-3.5 space-y-4">
                            {selectedBlock ? (
                                <>
                                    <div className="rounded-xl bg-white/5 border border-white/10 p-3">
                                        <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1">
                                            Active Caption Block
                                        </p>
                                        <p className="text-xs font-semibold text-white">
                                            &ldquo;{selectedBlock.text}&rdquo;{" "}
                                            <span>{selectedBlock.emoji}</span>
                                        </p>
                                        {selectedBlock.emoji && (
                                            <button
                                                type="button"
                                                onClick={() =>
                                                    onSetBlockEmoji(selectedBlock.id, "")
                                                }
                                                className="mt-2 text-[11px] text-red-400 hover:underline"
                                            >
                                                Remove emoji from this block
                                            </button>
                                        )}
                                    </div>

                                    <div>
                                        <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-2">
                                            Pick Emoji for Block
                                        </p>
                                        <div className="grid grid-cols-6 gap-1.5">
                                            {EMOJI_PALETTE.map((em) => (
                                                <button
                                                    key={em}
                                                    type="button"
                                                    onClick={() =>
                                                        onSetBlockEmoji(selectedBlock.id, em)
                                                    }
                                                    className={`h-9 rounded-lg flex items-center justify-center text-lg transition-all ${
                                                        selectedBlock.emoji === em
                                                            ? "bg-primary/30 border border-primary"
                                                            : "bg-white/5 hover:bg-white/15 border border-transparent"
                                                    }`}
                                                >
                                                    {em}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                </>
                            ) : (
                                <p className="text-xs text-slate-500">
                                    Select a caption block on the timeline to attach an emoji.
                                </p>
                            )}
                        </div>
                    </div>
                )}

                {/* BRAND & LOGO TAB */}
                {activeTab === "brand" && (
                    <div className="flex-1 overflow-y-auto custom-scrollbar p-3.5 space-y-5">
                        <div className="space-y-2.5">
                            <div className="flex items-center justify-between">
                                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
                                    Logo Watermark
                                </h3>
                                <label className="inline-flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={editState.logo.enabled}
                                        onChange={(e) => onToggleLogo(e.target.checked)}
                                        className="accent-primary"
                                    />
                                    Show
                                </label>
                            </div>

                            <input
                                ref={logoInputRef}
                                type="file"
                                accept="image/png,image/jpeg,image/webp"
                                className="hidden"
                                onChange={(e) => {
                                    const f = e.target.files?.[0];
                                    if (f) onUploadLogo(f);
                                }}
                            />

                            <button
                                type="button"
                                onClick={() => logoInputRef.current?.click()}
                                disabled={uploadingLogo}
                                className="w-full py-3 rounded-xl border border-dashed border-white/20 hover:border-primary/60 bg-white/[0.02] hover:bg-primary/5 flex flex-col items-center justify-center gap-1.5 transition-colors"
                            >
                                {editState.logo.asset_url ? (
                                    <img
                                        src={`${baseUrl}${editState.logo.asset_url}`}
                                        alt="Brand Logo"
                                        className="h-12 object-contain"
                                    />
                                ) : uploadingLogo ? (
                                    <Loader2 className="h-5 w-5 text-primary animate-spin" />
                                ) : (
                                    <Upload className="h-5 w-5 text-slate-400" />
                                )}
                                <span className="text-[11px] font-semibold text-slate-400">
                                    {editState.logo.asset_url
                                        ? "Click to replace logo (PNG/JPG)"
                                        : "Upload PNG / JPG Logo"}
                                </span>
                            </button>
                        </div>

                        {/* Brand Kits */}
                        <div className="space-y-2.5 pt-4 border-t border-white/10">
                            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
                                Brand Kits
                            </h3>
                            <div className="flex gap-1.5">
                                <input
                                    value={newKitName}
                                    onChange={(e) => setNewKitName(e.target.value)}
                                    placeholder="Save current look as..."
                                    className="flex-1 rounded-lg bg-white/5 border border-white/10 px-2.5 py-1.5 text-xs text-white placeholder-slate-500 focus:border-primary focus:outline-none"
                                />
                                <button
                                    type="button"
                                    onClick={() => {
                                        if (!newKitName.trim()) return;
                                        onSaveBrandKit(newKitName.trim());
                                        setNewKitName("");
                                    }}
                                    className="px-3 py-1.5 rounded-lg bg-primary hover:bg-primary/90 text-xs font-bold text-white flex items-center gap-1"
                                >
                                    <Plus className="h-3.5 w-3.5" />
                                    Save
                                </button>
                            </div>

                            <div className="space-y-2">
                                {brandKits.length === 0 ? (
                                    <p className="text-[11px] text-slate-500">
                                        Save your caption style &amp; logo as a reusable Brand Kit.
                                    </p>
                                ) : (
                                    brandKits.map((kit) => (
                                        <div
                                            key={kit.id}
                                            className="rounded-xl bg-white/[0.03] border border-white/10 p-2.5 flex items-center justify-between gap-2"
                                        >
                                            <button
                                                type="button"
                                                onClick={() => onApplyBrandKit(kit)}
                                                className="flex-1 text-left min-w-0"
                                            >
                                                <p className="text-xs font-bold text-white truncate hover:text-indigo-300">
                                                    {kit.name}
                                                </p>
                                                <p className="text-[10px] text-slate-500 truncate">
                                                    Style: {kit.caption_style}
                                                    {kit.logo_path ? " · Logo" : ""}
                                                </p>
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => onDeleteBrandKit(kit.id)}
                                                className="p-1.5 text-slate-500 hover:text-red-400"
                                                title="Delete Brand Kit"
                                            >
                                                <Trash2 className="h-3.5 w-3.5" />
                                            </button>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    </div>
                )}

                {/* LAYOUT / REFRAMING TAB */}
                {activeTab === "layout" && (
                    <div className="flex-1 overflow-y-auto custom-scrollbar p-3.5 space-y-3">
                        <div className="flex items-center justify-between">
                            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
                                9:16 Reframe Layout
                            </h3>
                            {relayouting && (
                                <Loader2 className="h-3.5 w-3.5 text-primary animate-spin" />
                            )}
                        </div>
                        <p className="text-[11px] text-slate-500">
                            Switch reframing strategy from the original source video without losing your caption edits.
                        </p>
                        <div className="space-y-2">
                            {LAYOUT_OPTIONS.map((opt) => {
                                const active = editState.layout_mode === opt.key;
                                return (
                                    <button
                                        key={opt.key}
                                        type="button"
                                        disabled={relayouting}
                                        onClick={() => onRelayout(opt.key)}
                                        className={`w-full rounded-xl p-3 text-left border transition-all disabled:opacity-50 ${
                                            active
                                                ? "border-primary bg-primary/15"
                                                : "border-white/10 bg-white/[0.02] hover:border-white/25"
                                        }`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className="text-xs font-bold text-white">
                                                {opt.name}
                                            </span>
                                            {active && (
                                                <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-primary text-white">
                                                    Active
                                                </span>
                                            )}
                                        </div>
                                        <p className="text-[11px] text-slate-400 mt-1 leading-snug">
                                            {opt.desc}
                                        </p>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                )}
            </div>
        </aside>
    );
}
