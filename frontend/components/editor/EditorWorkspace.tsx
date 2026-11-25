"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
    autoInjectEmojis,
    createBrandKit,
    deleteBrandKit,
    detectClipFillers,
    getApiBaseUrl,
    getBrandKits,
    getCaptionStyles,
    getClip,
    getClipWaveform,
    relayoutClip,
    renderClip,
    resyncClipWithWhisper,
    saveClipEditState,
    uploadLogoAsset,
} from "@/lib/api";
import {
    BrandKit,
    CaptionOverrides,
    CaptionStyle,
    Clip,
    ClipEditState,
    LogoConfig,
} from "@/lib/types";
import { EditorTopBar } from "./EditorTopBar";
import { EditorLeftRail, LeftRailTab } from "./EditorLeftRail";
import { EditorViewer } from "./EditorViewer";
import { EditorInspector } from "./EditorInspector";
import { EditorTimeline } from "./EditorTimeline";
import { YouTubePublishModal } from "@/components/project/YouTubePublishModal";

interface EditorWorkspaceProps {
    projectId: string;
    clipId: string;
}

const DEFAULT_EDIT_STATE: ClipEditState = {
    trim_start: 0,
    trim_end: 30,
    cuts: [],
    layout_mode: "dynamic_zoom",
    caption_enabled: true,
    caption_style: "mozi_pop",
    caption_overrides: {
        font: "Arial Black",
        fontsize: 96,
        primary_color: "#FFFFFF",
        highlight_color: "#FFE600",
        outline_color: "#000000",
        outline_width: 7,
        shadow: 4,
        background: false,
        bg_color: "#000000CC",
        animation: "jelly",
        position_y: 74,
        uppercase: true,
        max_words_per_line: 3,
        letter_spacing: 1,
    },
    caption_blocks: [],
    logo: {
        enabled: false,
        asset_url: null,
        x: 84,
        y: 7,
        scale: 18,
        opacity: 92,
    },
    brand_kit_id: null,
};

export function EditorWorkspace({ projectId, clipId }: EditorWorkspaceProps) {
    const [clip, setClip] = useState<Clip | null>(null);
    const [title, setTitle] = useState("");
    const [editState, setEditState] = useState<ClipEditState>(DEFAULT_EDIT_STATE);
    const [styles, setStyles] = useState<CaptionStyle[]>([]);
    const [brandKits, setBrandKits] = useState<BrandKit[]>([]);
    const [waveform, setWaveform] = useState<number[]>(Array(200).fill(0.25));

    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

    // Undo / Redo stacks
    const [past, setPast] = useState<ClipEditState[]>([]);
    const [future, setFuture] = useState<ClipEditState[]>([]);
    const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "unsaved" | "error">("saved");

    // UI selection & playback state
    const [leftTab, setLeftTab] = useState<LeftRailTab>("presets");
    const [selectedTrack, setSelectedTrack] = useState<"captions" | "logo" | "video">("captions");
    const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(30);
    const [isPlaying, setIsPlaying] = useState(false);
    const [playbackRate, setPlaybackRate] = useState(1);
    const [showSafeZones, setShowSafeZones] = useState(false);
    const [seekSignal, setSeekSignal] = useState<{ time: number; seq: number } | null>(null);

    // Async action loaders
    const [isRendering, setIsRendering] = useState(false);
    const [relayouting, setRelayouting] = useState(false);
    const [detectingFillers, setDetectingFillers] = useState(false);
    const [resyncingWhisper, setResyncingWhisper] = useState(false);
    const [injectingEmojis, setInjectingEmojis] = useState(false);
    const [uploadingLogo, setUploadingLogo] = useState(false);
    const [showYouTubeModal, setShowYouTubeModal] = useState(false);

    const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const hydratedRef = useRef(false);

    const showToast = useCallback((kind: "ok" | "err", text: string) => {
        setToast({ kind, text });
        setTimeout(() => setToast(null), 3500);
    }, []);

    // Load clip, styles, brand kits, and waveform
    useEffect(() => {
        setLoading(true);
        Promise.all([
            getClip(clipId),
            getCaptionStyles(),
            getBrandKits(),
            getClipWaveform(clipId, 200),
        ])
            .then(([clipData, styleList, kits, bars]) => {
                setClip(clipData);
                setTitle(clipData.title || "Untitled Clip");
                const loadedState: ClipEditState = clipData.edit_state
                    ? {
                          ...DEFAULT_EDIT_STATE,
                          ...clipData.edit_state,
                          caption_overrides: {
                              ...DEFAULT_EDIT_STATE.caption_overrides,
                              ...(clipData.edit_state.caption_overrides || {}),
                          },
                          logo: {
                              ...DEFAULT_EDIT_STATE.logo,
                              ...(clipData.edit_state.logo || {}),
                          },
                      }
                    : { ...DEFAULT_EDIT_STATE, trim_end: clipData.duration || 30 };

                setEditState(loadedState);
                setDuration(loadedState.trim_end || clipData.duration || 30);
                setStyles(styleList);
                setBrandKits(kits);
                setWaveform(bars);
                if (loadedState.caption_blocks.length > 0) {
                    setSelectedBlockId(loadedState.caption_blocks[0].id);
                }
                hydratedRef.current = true;
            })
            .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load clip"))
            .finally(() => setLoading(false));
    }, [clipId]);

    // Debounced auto-save
    const scheduleAutoSave = useCallback(
        (nextState: ClipEditState, nextTitle?: string) => {
            if (!hydratedRef.current) return;
            setSaveStatus("unsaved");
            if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
            saveTimerRef.current = setTimeout(async () => {
                setSaveStatus("saving");
                try {
                    await saveClipEditState(clipId, nextState, nextTitle ?? title);
                    setSaveStatus("saved");
                } catch {
                    setSaveStatus("error");
                }
            }, 700);
        },
        [clipId, title],
    );

    // Push state change into Undo stack + trigger auto-save
    const commitState = useCallback(
        (updater: (prev: ClipEditState) => ClipEditState, recordHistory = true) => {
            setEditState((prev) => {
                const next = updater(prev);
                if (recordHistory) {
                    setPast((h) => [...h.slice(-39), prev]);
                    setFuture([]);
                }
                scheduleAutoSave(next);
                return next;
            });
        },
        [scheduleAutoSave],
    );

    const handleUndo = useCallback(() => {
        setPast((h) => {
            if (h.length === 0) return h;
            const prev = h[h.length - 1];
            setFuture((f) => [editState, ...f.slice(0, 39)]);
            setEditState(prev);
            scheduleAutoSave(prev);
            return h.slice(0, -1);
        });
    }, [editState, scheduleAutoSave]);

    const handleRedo = useCallback(() => {
        setFuture((f) => {
            if (f.length === 0) return f;
            const next = f[0];
            setPast((h) => [...h.slice(-39), editState]);
            setEditState(next);
            scheduleAutoSave(next);
            return f.slice(1);
        });
    }, [editState, scheduleAutoSave]);

    const seekTo = useCallback((t: number) => {
        setCurrentTime(t);
        setSeekSignal((prev) => ({ time: t, seq: (prev?.seq ?? 0) + 1 }));
    }, []);

    // Split caption block at playhead
    const handleSplitBlockAtPlayhead = useCallback(() => {
        commitState((prev) => {
            const idx = prev.caption_blocks.findIndex(
                (b) =>
                    b.id === selectedBlockId ||
                    (currentTime >= b.start && currentTime <= b.end),
            );
            if (idx === -1) return prev;
            const blk = prev.caption_blocks[idx];
            if (blk.words.length < 2) return prev;

            let splitIdx = blk.words.findIndex((w) => w.start >= currentTime);
            if (splitIdx <= 0 || splitIdx >= blk.words.length) {
                splitIdx = Math.floor(blk.words.length / 2);
            }
            const leftWords = blk.words.slice(0, splitIdx);
            const rightWords = blk.words.slice(splitIdx);
            if (!leftWords.length || !rightWords.length) return prev;

            const leftBlk = {
                ...blk,
                end: leftWords[leftWords.length - 1].end,
                text: leftWords.map((w) => w.word).join(" "),
                words: leftWords,
            };
            const rightBlk = {
                id: `blk_${Math.random().toString(36).slice(2, 10)}`,
                start: rightWords[0].start,
                end: rightWords[rightWords.length - 1].end,
                text: rightWords.map((w) => w.word).join(" "),
                emoji: "",
                words: rightWords,
            };
            const nextBlocks = [...prev.caption_blocks];
            nextBlocks.splice(idx, 1, leftBlk, rightBlk);
            return { ...prev, caption_blocks: nextBlocks };
        });
    }, [commitState, currentTime, selectedBlockId]);

    // Keyboard shortcuts (Space, Ctrl+Z/Y, S)
    useEffect(() => {
        const onKeyDown = (e: KeyboardEvent) => {
            const tag = (e.target as HTMLElement)?.tagName;
            if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
            if (e.code === "Space") {
                e.preventDefault();
                setIsPlaying((p) => !p);
            } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) {
                e.preventDefault();
                handleUndo();
            } else if (
                (e.ctrlKey || e.metaKey) &&
                (e.key.toLowerCase() === "y" || (e.shiftKey && e.key.toLowerCase() === "z"))
            ) {
                e.preventDefault();
                handleRedo();
            } else if (e.key.toLowerCase() === "s" && !e.ctrlKey && !e.metaKey) {
                e.preventDefault();
                handleSplitBlockAtPlayhead();
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [handleUndo, handleRedo, handleSplitBlockAtPlayhead]);

    // Handlers
    const handleApplyPreset = (style: CaptionStyle) => {
        setSelectedTrack("captions");
        if (style.key === "none") {
            commitState((prev) => ({ ...prev, caption_enabled: false }));
            return;
        }
        commitState((prev) => ({
            ...prev,
            caption_enabled: true,
            caption_style: style.key,
            caption_overrides: {
                ...prev.caption_overrides,
                ...(style.config || {}),
                animation: style.animation,
            },
        }));
    };

    const handleRender = async () => {
        if (isRendering) return;
        setIsRendering(true);
        try {
            const updated = await renderClip(clipId, editState, title);
            setClip(updated);
            setSaveStatus("saved");
            showToast("ok", "Clip rendered & exported! Ready to download.");
        } catch (err) {
            showToast("err", err instanceof Error ? err.message : "Render failed");
        } finally {
            setIsRendering(false);
        }
    };

    const handleRelayout = async (layoutMode: string) => {
        if (relayouting) return;
        setRelayouting(true);
        try {
            await saveClipEditState(clipId, editState, title);
            const updated = await relayoutClip(clipId, layoutMode);
            setClip(updated);
            commitState((prev) => ({ ...prev, layout_mode: updated.layout_mode || layoutMode }), false);
            showToast("ok", `Layout switched to ${layoutMode}`);
        } catch (err) {
            showToast("err", err instanceof Error ? err.message : "Re-layout failed");
        } finally {
            setRelayouting(false);
        }
    };

    const handleRemoveFillers = () => {
        let count = 0;
        commitState((prev) => ({
            ...prev,
            caption_blocks: prev.caption_blocks.map((blk) => ({
                ...blk,
                words: blk.words.map((w) => {
                    if (w.is_filler && !w.deleted) {
                        count++;
                        return { ...w, deleted: true };
                    }
                    return w;
                }),
            })),
        }));
        showToast("ok", count > 0 ? `Marked ${count} filler word(s) for removal` : "No filler words found");
    };

    const handleDetectSilences = async () => {
        setDetectingFillers(true);
        try {
            const res = await detectClipFillers(clipId);
            if (res.silences.length === 0) {
                showToast("ok", "No long silent gaps detected");
            } else {
                commitState((prev) => ({
                    ...prev,
                    cuts: [...prev.cuts.filter((c) => c.reason !== "silence"), ...res.silences],
                }));
                showToast("ok", `Added ${res.silences.length} silence cut(s) to timeline`);
            }
        } catch {
            showToast("err", "Silence detection failed");
        } finally {
            setDetectingFillers(false);
        }
    };

    const handleResyncWhisper = async () => {
        if (resyncingWhisper) return;
        setResyncingWhisper(true);
        try {
            const res = await resyncClipWithWhisper(clipId);
            commitState((prev) => ({
                ...prev,
                caption_blocks: res.caption_blocks,
                caption_overrides: {
                    ...prev.caption_overrides,
                    sync_offset_ms: res.sync_offset_ms,
                },
            }));
            showToast("ok", "Captions re-synced directly to clip audio! Click Render & Export to save.");
        } catch (err) {
            showToast("err", err instanceof Error ? err.message : "Whisper sync failed");
        } finally {
            setResyncingWhisper(false);
        }
    };

    const handleAutoEmojis = async () => {
        setInjectingEmojis(true);
        try {
            const blocks = await autoInjectEmojis(clipId, editState);
            commitState((prev) => ({ ...prev, caption_blocks: blocks }));
            showToast("ok", "Injected contextual emojis across captions");
        } catch {
            showToast("err", "Failed to inject emojis");
        } finally {
            setInjectingEmojis(false);
        }
    };

    const handleUploadLogo = async (file: File) => {
        setUploadingLogo(true);
        try {
            const url = await uploadLogoAsset(file);
            setSelectedTrack("logo");
            commitState((prev) => ({
                ...prev,
                logo: { ...prev.logo, enabled: true, asset_url: url },
            }));
            showToast("ok", "Logo uploaded & added to V3 track");
        } catch (err) {
            showToast("err", err instanceof Error ? err.message : "Logo upload failed");
        } finally {
            setUploadingLogo(false);
        }
    };

    const handleSaveBrandKit = async (name: string) => {
        try {
            const created = await createBrandKit({
                name,
                logo_path: editState.logo.asset_url,
                caption_style: editState.caption_style,
                caption_overrides: editState.caption_overrides,
                logo_config: editState.logo,
            });
            setBrandKits((prev) => [created, ...prev]);
            showToast("ok", `Saved Brand Kit "${name}"`);
        } catch {
            showToast("err", "Failed to save Brand Kit");
        }
    };

    const handleApplyBrandKit = (kit: BrandKit) => {
        commitState((prev) => ({
            ...prev,
            brand_kit_id: kit.id,
            caption_enabled: kit.caption_style !== "none",
            caption_style: kit.caption_style || prev.caption_style,
            caption_overrides: {
                ...prev.caption_overrides,
                ...(kit.caption_overrides || {}),
            },
            logo: {
                ...prev.logo,
                ...(kit.logo_config || {}),
                asset_url: kit.logo_path ?? prev.logo.asset_url,
                enabled: Boolean(kit.logo_path),
            },
        }));
        showToast("ok", `Applied Brand Kit "${kit.name}"`);
    };

    const handleDeleteBrandKit = async (kitId: string) => {
        try {
            await deleteBrandKit(kitId);
            setBrandKits((prev) => prev.filter((k) => k.id !== kitId));
        } catch {
            showToast("err", "Could not delete Brand Kit");
        }
    };

    if (loading) {
        return (
            <div className="h-[calc(100vh-73px)] flex items-center justify-center bg-[#040407]">
                <div className="flex flex-col items-center gap-3">
                    <div className="h-9 w-9 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                    <p className="text-sm text-slate-400">Loading Studio Editor…</p>
                </div>
            </div>
        );
    }

    if (loadError || !clip) {
        return (
            <div className="h-[calc(100vh-73px)] flex items-center justify-center bg-[#040407]">
                <p className="text-red-400 text-sm font-semibold">{loadError || "Clip not found"}</p>
            </div>
        );
    }

    const baseUrl = getApiBaseUrl();
    const previewVideoUrl = `${baseUrl}${clip.clean_file_path || clip.file_path}`;
    const downloadUrl = `${baseUrl}${clip.file_path}`;

    return (
        <div className="h-[calc(100vh-73px)] flex flex-col bg-[#040407] overflow-hidden relative">
            {/* Toast */}
            {toast && (
                <div
                    className={`fixed bottom-6 right-6 z-50 px-4 py-2.5 rounded-xl text-xs font-bold shadow-2xl border ${
                        toast.kind === "ok"
                            ? "bg-emerald-950/95 border-emerald-500/40 text-emerald-200"
                            : "bg-red-950/95 border-red-500/40 text-red-200"
                    }`}
                >
                    {toast.text}
                </div>
            )}

            {/* Top Bar */}
            <EditorTopBar
                projectId={projectId}
                title={title}
                onTitleChange={(t) => {
                    setTitle(t);
                    scheduleAutoSave(editState, t);
                }}
                saveStatus={saveStatus}
                canUndo={past.length > 0}
                canRedo={future.length > 0}
                onUndo={handleUndo}
                onRedo={handleRedo}
                showSafeZones={showSafeZones}
                onToggleSafeZones={() => setShowSafeZones((s) => !s)}
                isRendering={isRendering}
                onRender={handleRender}
                onOpenYouTube={() => setShowYouTubeModal(true)}
                downloadUrl={downloadUrl}
            />

            {/* Middle 3-Panel Row: Left Rail | Center Viewer | Right Inspector */}
            <div className="flex-1 flex min-h-0 overflow-hidden">
                <EditorLeftRail
                    activeTab={leftTab}
                    onTabChange={setLeftTab}
                    editState={editState}
                    styles={styles}
                    brandKits={brandKits}
                    selectedBlockId={selectedBlockId}
                    currentTime={currentTime}
                    onApplyPreset={handleApplyPreset}
                    onSeek={seekTo}
                    onSelectBlock={(id) => {
                        setSelectedBlockId(id);
                        setSelectedTrack("captions");
                    }}
                    onToggleWordDeleted={(blockId, wordId) =>
                        commitState((prev) => ({
                            ...prev,
                            caption_blocks: prev.caption_blocks.map((b) =>
                                b.id !== blockId
                                    ? b
                                    : {
                                          ...b,
                                          words: b.words.map((w) =>
                                              w.id === wordId ? { ...w, deleted: !w.deleted } : w,
                                          ),
                                      },
                            ),
                        }))
                    }
                    onUpdateWordText={(blockId, wordId, text) =>
                        commitState((prev) => ({
                            ...prev,
                            caption_blocks: prev.caption_blocks.map((b) => {
                                if (b.id !== blockId) return b;
                                const words = b.words.map((w) =>
                                    w.id === wordId ? { ...w, word: text } : w,
                                );
                                return {
                                    ...b,
                                    words,
                                    text: words.map((w) => w.word).join(" "),
                                };
                            }),
                        }))
                    }
                    onRemoveFillers={handleRemoveFillers}
                    onDetectSilences={handleDetectSilences}
                    detectingFillers={detectingFillers}
                    onResyncWhisper={handleResyncWhisper}
                    resyncingWhisper={resyncingWhisper}
                    onAutoEmojis={handleAutoEmojis}
                    injectingEmojis={injectingEmojis}
                    onSetBlockEmoji={(blockId, emoji) =>
                        commitState((prev) => ({
                            ...prev,
                            caption_blocks: prev.caption_blocks.map((b) =>
                                b.id === blockId ? { ...b, emoji } : b,
                            ),
                        }))
                    }
                    onUploadLogo={handleUploadLogo}
                    uploadingLogo={uploadingLogo}
                    onToggleLogo={(enabled) =>
                        commitState((prev) => ({
                            ...prev,
                            logo: { ...prev.logo, enabled },
                        }))
                    }
                    onSaveBrandKit={handleSaveBrandKit}
                    onApplyBrandKit={handleApplyBrandKit}
                    onDeleteBrandKit={handleDeleteBrandKit}
                    onRelayout={handleRelayout}
                    relayouting={relayouting}
                />

                <EditorViewer
                    videoUrl={previewVideoUrl}
                    editState={editState}
                    currentTime={currentTime}
                    duration={duration}
                    isPlaying={isPlaying}
                    playbackRate={playbackRate}
                    showSafeZones={showSafeZones}
                    selectedTrack={selectedTrack}
                    onTimeUpdate={setCurrentTime}
                    onDurationLoaded={(d) => {
                        setDuration(d);
                        setEditState((prev) =>
                            prev.trim_end > d + 0.5 || prev.trim_end <= 0
                                ? { ...prev, trim_end: Number(d.toFixed(2)) }
                                : prev,
                        );
                    }}
                    onPlayStateChange={setIsPlaying}
                    onPlaybackRateChange={setPlaybackRate}
                    onSelectTrack={setSelectedTrack}
                    onUpdateCaptionY={(y) =>
                        commitState(
                            (prev) => ({
                                ...prev,
                                caption_overrides: { ...prev.caption_overrides, position_y: y },
                            }),
                            false,
                        )
                    }
                    onUpdateLogoPos={(x, y) =>
                        commitState(
                            (prev) => ({
                                ...prev,
                                logo: { ...prev.logo, x, y },
                            }),
                            false,
                        )
                    }
                    seekSignal={seekSignal}
                />

                <EditorInspector
                    clip={clip}
                    editState={editState}
                    duration={duration}
                    selectedTrack={selectedTrack}
                    selectedBlockId={selectedBlockId}
                    onSelectTrack={setSelectedTrack}
                    onUpdateOverrides={(patch: Partial<CaptionOverrides>) =>
                        commitState((prev) => ({
                            ...prev,
                            caption_overrides: { ...prev.caption_overrides, ...patch },
                        }))
                    }
                    onToggleCaptions={(enabled) =>
                        commitState((prev) => ({ ...prev, caption_enabled: enabled }))
                    }
                    onUpdateLogo={(patch: Partial<LogoConfig>) =>
                        commitState((prev) => ({
                            ...prev,
                            logo: { ...prev.logo, ...patch },
                        }))
                    }
                    onUpdateTrim={(trimStart, trimEnd) =>
                        commitState((prev) => ({
                            ...prev,
                            trim_start: Number(trimStart.toFixed(2)),
                            trim_end: Number(trimEnd.toFixed(2)),
                        }))
                    }
                    onRemoveCut={(cutId) =>
                        commitState((prev) => ({
                            ...prev,
                            cuts: prev.cuts.filter((c) => c.id !== cutId),
                        }))
                    }
                    onSeek={seekTo}
                    onUpdateBlock={(blockId, patch) =>
                        commitState((prev) => ({
                            ...prev,
                            caption_blocks: prev.caption_blocks.map((b) => {
                                if (b.id !== blockId) return b;
                                const nextStart = patch.start ?? b.start;
                                const nextEnd = patch.end ?? b.end;
                                let words = b.words;
                                if (patch.text !== undefined) {
                                    const toks = patch.text.trim().split(/\s+/).filter(Boolean);
                                    const dur = Math.max(0.2, nextEnd - nextStart);
                                    const step = dur / Math.max(toks.length, 1);
                                    words = toks.map((tok, i) => ({
                                        id: b.words[i]?.id || `w_${Math.random().toString(36).slice(2, 8)}`,
                                        word: tok,
                                        start: Number((nextStart + i * step).toFixed(3)),
                                        end: Number((nextStart + (i + 1) * step).toFixed(3)),
                                        deleted: false,
                                        is_filler: false,
                                    }));
                                }
                                return {
                                    ...b,
                                    start: nextStart,
                                    end: nextEnd,
                                    text: patch.text ?? b.text,
                                    emoji: patch.emoji ?? b.emoji,
                                    words,
                                };
                            }),
                        }))
                    }
                    onDeleteBlock={(blockId) => {
                        commitState((prev) => ({
                            ...prev,
                            caption_blocks: prev.caption_blocks.filter((b) => b.id !== blockId),
                        }));
                        setSelectedBlockId(null);
                    }}
                />
            </div>

            {/* Bottom Multi-Track Timeline */}
            <EditorTimeline
                editState={editState}
                duration={duration}
                currentTime={currentTime}
                waveform={waveform}
                selectedBlockId={selectedBlockId}
                selectedTrack={selectedTrack}
                onSeek={seekTo}
                onSelectBlock={setSelectedBlockId}
                onSelectTrack={setSelectedTrack}
                onUpdateTrim={(s, e) =>
                    commitState(
                        (prev) => ({
                            ...prev,
                            trim_start: Number(s.toFixed(2)),
                            trim_end: Number(e.toFixed(2)),
                        }),
                        false,
                    )
                }
                onUpdateBlockTiming={(blockId, s, e) =>
                    commitState(
                        (prev) => ({
                            ...prev,
                            caption_blocks: prev.caption_blocks.map((b) => {
                                if (b.id !== blockId) return b;
                                const dur = Math.max(0.1, e - s);
                                const step = dur / Math.max(b.words.length, 1);
                                return {
                                    ...b,
                                    start: Number(s.toFixed(2)),
                                    end: Number(e.toFixed(2)),
                                    words: b.words.map((w, idx) => ({
                                        ...w,
                                        start: Number((s + idx * step).toFixed(3)),
                                        end: Number((s + (idx + 1) * step).toFixed(3)),
                                    })),
                                };
                            }),
                        }),
                        false,
                    )
                }
                onSplitBlockAtPlayhead={handleSplitBlockAtPlayhead}
                onMergeWithNextBlock={() => {
                    if (!selectedBlockId) return;
                    commitState((prev) => {
                        const idx = prev.caption_blocks.findIndex((b) => b.id === selectedBlockId);
                        if (idx === -1 || idx + 1 >= prev.caption_blocks.length) return prev;
                        const a = prev.caption_blocks[idx];
                        const b = prev.caption_blocks[idx + 1];
                        const merged = {
                            ...a,
                            end: b.end,
                            text: `${a.text} ${b.text}`.trim(),
                            emoji: a.emoji || b.emoji || "",
                            words: [...a.words, ...b.words],
                        };
                        const next = [...prev.caption_blocks];
                        next.splice(idx, 2, merged);
                        return { ...prev, caption_blocks: next };
                    });
                }}
                onDeleteSelectedBlock={() => {
                    if (!selectedBlockId) return;
                    commitState((prev) => ({
                        ...prev,
                        caption_blocks: prev.caption_blocks.filter((b) => b.id !== selectedBlockId),
                    }));
                    setSelectedBlockId(null);
                }}
                onAddCutAtPlayhead={() => {
                    const st = Math.max(editState.trim_start, currentTime);
                    const en = Math.min(editState.trim_end, st + 1.0);
                    if (en - st < 0.2) return;
                    commitState((prev) => ({
                        ...prev,
                        cuts: [
                            ...prev.cuts,
                            {
                                id: `cut_${Math.random().toString(36).slice(2, 8)}`,
                                start: Number(st.toFixed(2)),
                                end: Number(en.toFixed(2)),
                                reason: "manual",
                            },
                        ],
                    }));
                    setSelectedTrack("video");
                }}
            />

            {showYouTubeModal && (
                <YouTubePublishModal
                    mode="single"
                    clip={{ ...clip, title }}
                    onClose={() => setShowYouTubeModal(false)}
                    onPublished={(res) => {
                        setClip((prev) =>
                            prev
                                ? {
                                      ...prev,
                                      youtube_video_id: res.video_id,
                                      youtube_url: res.url,
                                      youtube_status: res.status,
                                      youtube_scheduled_at: res.publish_at,
                                  }
                                : prev,
                        );
                        showToast("ok", "Uploaded to YouTube Shorts!");
                    }}
                />
            )}
        </div>
    );
}
