"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
    createBatchJob,
    createProject,
    getDiscoverNiches,
    getSettings,
    getTrendingVideos,
    runAiScout,
} from "@/lib/api";
import { AiScoutPick, DiscoverNiche, Settings, TrendingVideo } from "@/lib/types";
import {
    Flame,
    Search,
    Sparkles,
    Scissors,
    ExternalLink,
    CheckSquare,
    Square,
    Layers,
    Loader2,
    BadgeCheck,
    Eye,
    Clock,
} from "lucide-react";

const PROVIDER_LABELS: Record<Settings["llm_provider"], string> = {
    codex: "Codex CLI",
    claude: "Claude CLI",
    openai: "OpenAI",
    anthropic: "Anthropic",
    gemini: "Gemini",
    ollama: "Ollama",
};

function formatViews(n: number): string {
    if (!n) return "—";
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M views`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K views`;
    return `${n} views`;
}

function formatDuration(sec: number): string {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
    return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function DiscoverPage() {
    const router = useRouter();
    const [niches, setNiches] = useState<DiscoverNiche[]>([]);
    const [activeNiche, setActiveNiche] = useState("podcasts");
    const [customQuery, setCustomQuery] = useState("");
    const [minMinutes, setMinMinutes] = useState(5);
    const [sortBy, setSortBy] = useState<"score" | "views" | "duration">("score");

    const [videos, setVideos] = useState<TrendingVideo[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // AI Scout state
    const [scoutGoal, setScoutGoal] = useState("");
    const [scouting, setScouting] = useState(false);
    const [scoutPicks, setScoutPicks] = useState<Record<string, AiScoutPick>>({});
    const [activeProvider, setActiveProvider] = useState<Settings["llm_provider"]>("codex");
    const [scoutProvider, setScoutProvider] = useState<string | null>(null);

    // Selection & actions
    const [selectedIds, setSelectedIds] = useState<string[]>([]);
    const [clippingId, setClippingId] = useState<string | null>(null);
    const [batchSending, setBatchSending] = useState(false);

    const loadVideos = useCallback(
        async (nicheKey: string, q: string, minM: number, sort: "score" | "views" | "duration") => {
            setLoading(true);
            setError(null);
            setScoutPicks({});
            setSelectedIds([]);
            try {
                const list = await getTrendingVideos({
                    niche: nicheKey,
                    query: q.trim() || undefined,
                    min_minutes: minM,
                    limit: 15,
                    sort,
                });
                setVideos(list);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to fetch trending videos");
            } finally {
                setLoading(false);
            }
        },
        [],
    );

    useEffect(() => {
        getDiscoverNiches().then(setNiches).catch(console.error);
        getSettings()
            .then((s) => {
                if (s.llm_provider) setActiveProvider(s.llm_provider);
            })
            .catch(console.error);
        loadVideos("podcasts", "", 5, "score");
    }, [loadVideos]);

    const handleSearchSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        loadVideos(activeNiche, customQuery, minMinutes, sortBy);
    };

    const handleAiScout = async () => {
        if (scouting || videos.length === 0) return;
        setScouting(true);
        setError(null);
        try {
            const res = await runAiScout(videos, scoutGoal, activeProvider);
            const map: Record<string, AiScoutPick> = {};
            for (const p of res.picks) map[p.video_id] = p;
            setScoutPicks(map);
            setScoutProvider(res.provider);
            // Sort AI picks to the top!
            setVideos((prev) =>
                [...prev].sort((a, b) => {
                    const pa = map[a.id]?.ai_score ?? 0;
                    const pb = map[b.id]?.ai_score ?? 0;
                    if (pb !== pa) return pb - pa;
                    return b.clip_score - a.clip_score;
                }),
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "AI Scout failed");
        } finally {
            setScouting(false);
        }
    };

    const handleClipNow = async (video: TrendingVideo) => {
        if (clippingId) return;
        setClippingId(video.id);
        const pick = scoutPicks[video.id];
        try {
            const { project_id } = await createProject(video.url, {
                clip_min_duration: 30,
                clip_max_duration: 60,
                layout_preference: pick?.recommended_layout || video.recommended_layout || "auto",
                custom_prompt: pick?.suggested_prompt || undefined,
            });
            router.push(`/project/${project_id}`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not start project");
            setClippingId(null);
        }
    };

    const toggleSelect = (id: string) => {
        setSelectedIds((prev) =>
            prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
        );
    };

    const handleSendToBatch = async () => {
        if (selectedIds.length === 0 || batchSending) return;
        setBatchSending(true);
        try {
            const urls = videos.filter((v) => selectedIds.includes(v.id)).map((v) => v.url);
            await createBatchJob(urls, {
                clip_min_duration: 30,
                clip_max_duration: 60,
                layout_preference: "auto",
                custom_prompt: scoutGoal.trim() || undefined,
            });
            router.push("/batch");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Batch submission failed");
            setBatchSending(false);
        }
    };

    const aiPickCount = Object.keys(scoutPicks).length;

    return (
        <div className="mx-auto w-full max-w-7xl px-6 lg:px-14 py-10 space-y-8 pb-24">
            {/* Header */}
            <div className="flex items-start justify-between gap-6 flex-wrap">
                <div>
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-orange-500/15 border border-orange-500/30 px-3.5 py-1 text-xs font-bold uppercase tracking-widest text-orange-300 mb-3">
                        <Flame className="h-3.5 w-3.5" />
                        Trending Clip Scout
                    </span>
                    <h1 className="text-3xl font-bold text-white">
                        Find trending videos <span className="text-gradient">worth clipping</span>
                    </h1>
                    <p className="text-sm text-slate-400 mt-1.5 max-w-2xl">
                        Discovers high-performing long-form YouTube videos (podcasts, interviews, breakdowns), scores them for short-form clip potential, and lets your AI scout the best viral angles.
                    </p>
                </div>
            </div>

            {/* Search & Filters Card */}
            <div className="glass-card rounded-3xl p-6 space-y-5">
                {/* Niche Pills */}
                <div className="flex flex-wrap gap-2">
                    {(niches.length > 0
                        ? niches
                        : [
                              { key: "podcasts", name: "Podcasts & Interviews", query: "", default_layout: "podcast_split" },
                              { key: "ai_tech", name: "AI & Tech", query: "", default_layout: "dynamic_zoom" },
                              { key: "business", name: "Business & Startups", query: "", default_layout: "podcast_split" },
                              { key: "finance", name: "Finance & Investing", query: "", default_layout: "dynamic_zoom" },
                              { key: "gaming", name: "Gaming & Creators", query: "", default_layout: "tutorial" },
                              { key: "health", name: "Health & Fitness", query: "", default_layout: "podcast_split" },
                              { key: "sports", name: "Sports & Hot Takes", query: "", default_layout: "podcast_split" },
                          ]
                    ).map((n) => (
                        <button
                            key={n.key}
                            type="button"
                            onClick={() => {
                                setActiveNiche(n.key);
                                setCustomQuery("");
                                loadVideos(n.key, "", minMinutes, sortBy);
                            }}
                            className={`px-3.5 py-1.5 rounded-full text-xs font-bold border transition-all ${
                                activeNiche === n.key && !customQuery
                                    ? "bg-primary border-primary text-white shadow-lg"
                                    : "bg-white/5 border-white/10 text-slate-300 hover:border-white/25 hover:text-white"
                            }`}
                        >
                            {n.name}
                        </button>
                    ))}
                </div>

                {/* Custom Search + Min Length + Sort */}
                <form onSubmit={handleSearchSubmit} className="grid grid-cols-1 md:grid-cols-12 gap-3">
                    <div className="md:col-span-6 flex items-center gap-2 rounded-xl bg-slate-950/70 border border-white/10 px-3.5 py-2 focus-within:border-primary">
                        <Search className="h-4 w-4 text-slate-400 flex-shrink-0" />
                        <input
                            value={customQuery}
                            onChange={(e) => setCustomQuery(e.target.value)}
                            placeholder="Search any topic, creator, or podcast (e.g. Lex Fridman, Nvidia, startups)..."
                            className="flex-1 bg-transparent text-sm text-white placeholder-slate-500 focus:outline-none"
                        />
                    </div>
                    <select
                        value={minMinutes}
                        onChange={(e) => {
                            const m = Number(e.target.value);
                            setMinMinutes(m);
                            loadVideos(activeNiche, customQuery, m, sortBy);
                        }}
                        className="md:col-span-2 rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2.5 text-xs font-semibold text-white"
                    >
                        <option value={3} className="bg-slate-900">Min 3+ min</option>
                        <option value={10} className="bg-slate-900">Min 10+ min</option>
                        <option value={25} className="bg-slate-900">Min 25+ min</option>
                    </select>
                    <select
                        value={sortBy}
                        onChange={(e) => {
                            const s = e.target.value as "score" | "views" | "duration";
                            setSortBy(s);
                            loadVideos(activeNiche, customQuery, minMinutes, s);
                        }}
                        className="md:col-span-2 rounded-xl bg-slate-900/70 border border-white/10 px-3 py-2.5 text-xs font-semibold text-white"
                    >
                        <option value="score" className="bg-slate-900">Sort: Clip Score</option>
                        <option value="views" className="bg-slate-900">Sort: Most Viewed</option>
                        <option value="duration" className="bg-slate-900">Sort: Longest</option>
                    </select>
                    <button
                        type="submit"
                        disabled={loading}
                        className="md:col-span-2 rounded-xl bg-white/10 hover:bg-white/15 border border-white/15 text-xs font-bold text-white py-2.5 transition-colors"
                    >
                        Search
                    </button>
                </form>

                {/* AI Scout Bar */}
                <div className="pt-4 border-t border-white/10 flex flex-col md:flex-row gap-3 items-stretch md:items-center">
                    <select
                        value={activeProvider}
                        onChange={(e) => setActiveProvider(e.target.value as Settings["llm_provider"])}
                        aria-label="AI Scout Provider"
                        className="rounded-xl bg-slate-900/70 border border-indigo-500/25 px-3 py-2.5 text-xs font-bold text-indigo-200 focus:border-primary focus:outline-none flex-shrink-0"
                    >
                        {(["codex", "claude", "openai", "anthropic", "gemini", "ollama"] as const).map((p) => (
                            <option key={p} value={p} className="bg-slate-900 text-white">
                                {PROVIDER_LABELS[p]}
                            </option>
                        ))}
                    </select>
                    <input
                        value={scoutGoal}
                        onChange={(e) => setScoutGoal(e.target.value)}
                        placeholder={`Optional audience/niche goal for ${PROVIDER_LABELS[activeProvider]} Scout (e.g. 'Contrarian AI & money hooks for TikTok')...`}
                        className="flex-1 rounded-xl bg-slate-950/60 border border-indigo-500/20 px-4 py-2.5 text-xs text-white placeholder-slate-500 focus:border-primary focus:outline-none"
                    />
                    <button
                        type="button"
                        onClick={handleAiScout}
                        disabled={scouting || loading || videos.length === 0}
                        className="px-5 py-2.5 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-50 text-xs font-bold text-white flex items-center justify-center gap-2 glow-primary flex-shrink-0 transition-all"
                    >
                        {scouting ? (
                            <>
                                <Loader2 className="h-4 w-4 animate-spin" />
                                {PROVIDER_LABELS[activeProvider]} Scouting Top Picks…
                            </>
                        ) : (
                            <>
                                <Sparkles className="h-4 w-4" />
                                Ask {PROVIDER_LABELS[activeProvider]} to Scout Top 5
                            </>
                        )}
                    </button>
                </div>

                {aiPickCount > 0 && (
                    <div className="rounded-xl bg-indigo-500/10 border border-indigo-500/30 px-4 py-2.5 text-xs text-indigo-200 flex items-center justify-between">
                        <span>
                            ✨ <strong>{(scoutProvider && PROVIDER_LABELS[scoutProvider as Settings["llm_provider"]]) || scoutProvider?.toUpperCase() || "AI"}</strong> ranked the top{" "}
                            <strong>{aiPickCount}</strong> videos to the front with tailored hook angles &amp; focus prompts!
                        </span>
                    </div>
                )}
            </div>

            {error && (
                <div className="rounded-2xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-sm text-red-400">
                    {error}
                </div>
            )}

            {/* Video Grid */}
            {loading ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {[...Array(6)].map((_, i) => (
                        <div key={i} className="glass rounded-2xl overflow-hidden animate-pulse">
                            <div className="aspect-video bg-white/5" />
                            <div className="p-4 space-y-2.5">
                                <div className="h-4 w-5/6 rounded bg-white/5" />
                                <div className="h-3 w-1/2 rounded bg-white/5" />
                            </div>
                        </div>
                    ))}
                </div>
            ) : videos.length === 0 ? (
                <div className="glass rounded-3xl p-14 text-center text-slate-400">
                    No matching long-form videos found. Try lowering the minimum duration or searching another topic.
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {videos.map((v) => {
                        const pick = scoutPicks[v.id];
                        const isSelected = selectedIds.includes(v.id);
                        return (
                            <div
                                key={v.id}
                                className={`rounded-2xl overflow-hidden flex flex-col border transition-all ${
                                    pick
                                        ? "bg-indigo-950/25 border-indigo-400/50 shadow-[0_0_30px_rgba(46,29,237,0.2)]"
                                        : isSelected
                                          ? "glass border-primary/60"
                                          : "glass border-white/10 hover:border-white/25"
                                }`}
                            >
                                {/* Thumbnail */}
                                <div className="relative aspect-video bg-slate-900 overflow-hidden">
                                    <img
                                        src={v.thumbnail}
                                        alt={v.title}
                                        className="w-full h-full object-cover"
                                    />
                                    {/* Top Left: Select checkbox + AI Pick badge */}
                                    <div className="absolute top-2.5 left-2.5 flex items-center gap-1.5">
                                        <button
                                            type="button"
                                            onClick={() => toggleSelect(v.id)}
                                            className="p-1.5 rounded-lg bg-black/75 hover:bg-black text-white backdrop-blur-md"
                                            title="Select for Batch Queue"
                                        >
                                            {isSelected ? (
                                                <CheckSquare className="h-4 w-4 text-primary" />
                                            ) : (
                                                <Square className="h-4 w-4 text-slate-300" />
                                            )}
                                        </button>
                                        {pick && (
                                            <span className="px-2.5 py-1 rounded-lg bg-primary text-white text-[10px] font-black uppercase tracking-wider shadow-lg flex items-center gap-1">
                                                <Sparkles className="h-3 w-3" />
                                                AI Pick {pick.ai_score}/10
                                            </span>
                                        )}
                                    </div>
                                    {/* Top Right: Clip Potential Score */}
                                    <div
                                        className={`absolute top-2.5 right-2.5 px-2.5 py-1 rounded-lg text-[11px] font-black backdrop-blur-md border ${
                                            v.clip_score >= 80
                                                ? "bg-emerald-950/85 border-emerald-400/40 text-emerald-300"
                                                : v.clip_score >= 65
                                                  ? "bg-indigo-950/85 border-indigo-400/40 text-indigo-200"
                                                  : "bg-black/75 border-white/15 text-slate-300"
                                        }`}
                                    >
                                        {v.clip_score} Score
                                    </div>
                                    {/* Bottom Right: Duration */}
                                    <span className="absolute bottom-2.5 right-2.5 px-2 py-0.5 rounded bg-black/80 text-[11px] font-mono font-bold text-white flex items-center gap-1">
                                        <Clock className="h-3 w-3 text-slate-400" />
                                        {formatDuration(v.duration)}
                                    </span>
                                </div>

                                {/* Body */}
                                <div className="p-4 flex-1 flex flex-col justify-between gap-3">
                                    <div className="space-y-2">
                                        <h3 className="text-sm font-bold text-white line-clamp-2 leading-snug" title={v.title}>
                                            {v.title}
                                        </h3>
                                        <div className="flex items-center justify-between text-xs text-slate-400 gap-2">
                                            <span className="flex items-center gap-1 truncate">
                                                {v.channel}
                                                {v.channel_verified && <BadgeCheck className="h-3.5 w-3.5 text-sky-400 flex-shrink-0" />}
                                            </span>
                                            <span className="flex items-center gap-1 flex-shrink-0 font-mono text-[11px]">
                                                <Eye className="h-3 w-3" />
                                                {formatViews(v.view_count)}
                                            </span>
                                        </div>

                                        {/* Heuristic Signals */}
                                        <div className="flex flex-wrap gap-1">
                                            {v.signals.map((sig) => (
                                                <span
                                                    key={sig}
                                                    className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-slate-300"
                                                >
                                                    {sig}
                                                </span>
                                            ))}
                                            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-primary/15 text-indigo-300">
                                                {pick?.recommended_layout || v.recommended_layout}
                                            </span>
                                        </div>

                                        {/* AI Scout Hook Angle */}
                                        {pick && (
                                            <div className="rounded-xl bg-indigo-500/10 border border-indigo-500/25 p-2.5 space-y-1 text-[11px]">
                                                <p className="text-indigo-200 font-semibold leading-snug">
                                                    💡 {pick.hook_angle}
                                                </p>
                                                {pick.suggested_prompt && (
                                                    <p className="text-slate-400 italic line-clamp-2">
                                                        Focus: &ldquo;{pick.suggested_prompt}&rdquo;
                                                    </p>
                                                )}
                                            </div>
                                        )}
                                    </div>

                                    {/* Actions */}
                                    <div className="flex gap-2 pt-2 border-t border-white/5">
                                        <button
                                            type="button"
                                            disabled={clippingId === v.id}
                                            onClick={() => handleClipNow(v)}
                                            className="flex-1 py-2 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-60 text-xs font-bold text-white flex items-center justify-center gap-1.5 transition-all"
                                        >
                                            {clippingId === v.id ? (
                                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                            ) : (
                                                <Scissors className="h-3.5 w-3.5" />
                                            )}
                                            {clippingId === v.id ? "Starting…" : "Clip Now"}
                                        </button>
                                        <a
                                            href={v.url}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="px-3 py-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-xs font-semibold text-slate-300 flex items-center gap-1"
                                            title="Watch on YouTube"
                                        >
                                            <ExternalLink className="h-3.5 w-3.5" />
                                        </a>
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* Floating Batch Bar when videos selected */}
            {selectedIds.length > 0 && (
                <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 glass-dark border border-primary/50 rounded-2xl px-5 py-3 shadow-2xl flex items-center gap-4">
                    <span className="text-xs font-bold text-white">
                        {selectedIds.length} video{selectedIds.length === 1 ? "" : "s"} selected
                    </span>
                    <button
                        type="button"
                        onClick={handleSendToBatch}
                        disabled={batchSending}
                        className="px-4 py-2 rounded-xl bg-primary hover:bg-primary/90 text-xs font-bold text-white flex items-center gap-1.5 glow-primary"
                    >
                        {batchSending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Layers className="h-3.5 w-3.5" />}
                        Send to Batch Queue →
                    </button>
                    <button
                        type="button"
                        onClick={() => setSelectedIds([])}
                        className="text-xs text-slate-400 hover:text-white"
                    >
                        Clear
                    </button>
                </div>
            )}
        </div>
    );
}
