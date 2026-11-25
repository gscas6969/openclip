import {
    Project,
    Clip,
    ClipEditState,
    BrandKit,
    BatchJob,
    QuickToolResult,
    McpConfigInfo,
    CodexStatus,
    DiscoverNiche,
    TrendingVideo,
    AiScoutPick,
    YouTubeStatus,
    YouTubePublishResult,
    StorageStats,
    CaptionBlock,
    CutSegment,
    Settings,
    CaptionStyle,
    SystemHealth,
    InstallJob,
} from './types';

function getBaseUrl(): string {
    if (typeof window !== 'undefined') {
        const host = window.location.hostname;
        if (host !== 'localhost' && host !== '127.0.0.1') {
            return `https://${host.replace(/^(\d+)-/, '8000-')}`;
        }
    }
    return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
}

export function getApiBaseUrl(): string {
    return getBaseUrl();
}

export const getProjects = async (): Promise<Project[]> => {
    try {
        const res = await fetch(`${getBaseUrl()}/api/projects`, { cache: 'no-store' });
        if (!res.ok) return [];
        return res.json();
    } catch {
        return [];
    }
};

export const getProject = async (id: string): Promise<Project & { clips: Clip[] }> => {
    const res = await fetch(`${getBaseUrl()}/api/projects/${id}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Project not found');
    return res.json();
};

export interface CreateProjectOptions {
    clip_min_duration?: number;
    clip_max_duration?: number;
    custom_prompt?: string;
    layout_preference?: string;
    brand_kit_id?: string | null;
}

export const createProject = async (
    youtube_url: string,
    options?: CreateProjectOptions,
): Promise<{ project_id: string }> => {
    const res = await fetch(`${getBaseUrl()}/api/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            youtube_url,
            clip_min_duration: options?.clip_min_duration ?? 30,
            clip_max_duration: options?.clip_max_duration ?? 60,
            custom_prompt: options?.custom_prompt || null,
            layout_preference: options?.layout_preference ?? 'auto',
            brand_kit_id: options?.brand_kit_id || null,
        }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to create project');
    }
    return res.json();
};

export const uploadProjectVideo = async (
    file: File,
    options?: CreateProjectOptions & { title?: string },
): Promise<{ project_id: string }> => {
    const form = new FormData();
    form.append('file', file);
    if (options?.title) form.append('title', options.title);
    form.append('clip_min_duration', String(options?.clip_min_duration ?? 30));
    form.append('clip_max_duration', String(options?.clip_max_duration ?? 60));
    if (options?.custom_prompt) form.append('custom_prompt', options.custom_prompt);
    form.append('layout_preference', options?.layout_preference ?? 'auto');
    if (options?.brand_kit_id) form.append('brand_kit_id', options.brand_kit_id);

    const res = await fetch(`${getBaseUrl()}/api/projects/upload`, {
        method: 'POST',
        body: form,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to upload video');
    }
    return res.json();
};

export const retryProject = async (id: string): Promise<void> => {
    const res = await fetch(`${getBaseUrl()}/api/projects/${id}/retry`, { method: 'POST' });
    if (!res.ok && res.status !== 409) throw new Error('Failed to retry project');
};

export const deleteProject = async (id: string): Promise<void> => {
    const res = await fetch(`${getBaseUrl()}/api/projects/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to delete project');
};

export function getExportAllUrl(projectId: string): string {
    return `${getBaseUrl()}/api/projects/${projectId}/export-all`;
}

// ---------------------------------------------------------------------------
// Clip Editor API
// ---------------------------------------------------------------------------

export async function getClip(clipId: string): Promise<Clip> {
    const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Clip not found');
    return res.json();
}

export async function saveClipEditState(
    clipId: string,
    edit_state: ClipEditState,
    title?: string,
): Promise<Clip> {
    const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}/edit-state`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edit_state, title }),
    });
    if (!res.ok) throw new Error('Failed to save edit state');
    return res.json();
}

export async function renderClip(
    clipId: string,
    edit_state: ClipEditState,
    title?: string,
): Promise<Clip> {
    const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}/render`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edit_state, title }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Render failed');
    }
    return res.json();
}

export async function relayoutClip(clipId: string, layout_mode: string): Promise<Clip> {
    const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}/layout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout_mode }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Layout re-render failed');
    }
    return res.json();
}

export async function relayoutProject(
    projectId: string,
    layout_mode: string,
): Promise<Project & { clips: Clip[] }> {
    const res = await fetch(`${getBaseUrl()}/api/projects/${projectId}/relayout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout_mode }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Project re-layout failed');
    }
    return res.json();
}

export async function getClipWaveform(clipId: string, bars = 200): Promise<number[]> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}/waveform?bars=${bars}`, {
            cache: 'no-store',
        });
        if (!res.ok) return Array(bars).fill(0.25);
        const data = await res.json();
        return data.bars || Array(bars).fill(0.25);
    } catch {
        return Array(bars).fill(0.25);
    }
}

export async function autoInjectEmojis(
    clipId: string,
    edit_state: ClipEditState,
): Promise<CaptionBlock[]> {
    const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}/auto-emojis`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edit_state }),
    });
    if (!res.ok) throw new Error('Failed to inject emojis');
    const data = await res.json();
    return data.caption_blocks || [];
}

export async function detectClipFillers(
    clipId: string,
): Promise<{ silences: CutSegment[]; filler_word_ids: string[] }> {
    const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}/detect-fillers`, {
        method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to detect fillers and silences');
    return res.json();
}

export async function uploadLogoAsset(file: File): Promise<string> {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${getBaseUrl()}/api/assets/logo`, {
        method: 'POST',
        body: form,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to upload logo');
    }
    const data = await res.json();
    return data.asset_url;
}

// ---------------------------------------------------------------------------
// Brand Kits API
// ---------------------------------------------------------------------------

export async function getBrandKits(): Promise<BrandKit[]> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/brand-kits`, { cache: 'no-store' });
        if (!res.ok) return [];
        return res.json();
    } catch {
        return [];
    }
}

export async function createBrandKit(kit: Omit<BrandKit, 'id' | 'created_at'>): Promise<BrandKit> {
    const res = await fetch(`${getBaseUrl()}/api/brand-kits`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(kit),
    });
    if (!res.ok) throw new Error('Failed to create brand kit');
    return res.json();
}

export async function deleteBrandKit(kitId: string): Promise<void> {
    const res = await fetch(`${getBaseUrl()}/api/brand-kits/${kitId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to delete brand kit');
}

// ---------------------------------------------------------------------------
// Settings & Styles
// ---------------------------------------------------------------------------

export async function getSettings(): Promise<Settings> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/settings`);
        if (!res.ok) {
            return {
                llm_provider: 'openai',
                llm_model: 'gpt-4o',
                whisper_model: 'base',
                caption_style: 'mozi_pop',
                video_encoder: 'auto',
                has_api_key: false,
            };
        }
        return res.json();
    } catch {
        return {
            llm_provider: 'openai',
            llm_model: 'gpt-4o',
            whisper_model: 'base',
            caption_style: 'mozi_pop',
            video_encoder: 'auto',
            has_api_key: false,
        };
    }
}

export async function getEncoderStatus(refresh = false): Promise<import('./types').EncoderStatus | null> {
    try {
        const qs = refresh ? '?refresh=true' : '';
        const res = await fetch(`${getBaseUrl()}/api/system/encoders${qs}`, { cache: 'no-store' });
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

export async function saveSettings(
    settings: Partial<Settings> & { llm_api_key?: string },
): Promise<void> {
    const res = await fetch(`${getBaseUrl()}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
    });
    if (!res.ok) throw new Error('Failed to save settings');
}

export async function getCaptionStyles(): Promise<CaptionStyle[]> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/caption-styles`);
        if (res.ok) return res.json();
    } catch {
        // fallback below
    }
    return [
        {
            key: 'none',
            name: 'No Captions',
            category: 'clean',
            animation: 'none',
            preview_colors: { text: '#64748B', highlight: null, background: null },
        },
        {
            key: 'mozi_pop',
            name: 'Mozi',
            category: 'kinetic',
            animation: 'jelly',
            preview_colors: { text: '#FFFFFF', highlight: '#FFE600', background: null },
        },
        {
            key: 'tiktok_style',
            name: 'TikTok',
            category: 'kinetic',
            animation: 'highlight',
            preview_colors: { text: '#FFFFFF', highlight: '#FFE600', background: null },
        },
        {
            key: 'viral_word',
            name: 'Viral Word',
            category: 'kinetic',
            animation: 'one_word',
            preview_colors: { text: '#FFFFFF', highlight: '#FFE600', background: null },
        },
    ];
}

// ---------------------------------------------------------------------------
// System requirements
// ---------------------------------------------------------------------------

export async function getSystemHealth(): Promise<SystemHealth | null> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/health`, { cache: 'no-store' });
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

export async function startSystemInstall(): Promise<InstallJob> {
    const res = await fetch(`${getBaseUrl()}/api/system/install`, {
        method: 'POST',
        headers: { 'X-OpenClip-Install': '1' },
    });
    if (!res.ok) throw new Error('Could not start the install');
    const data = await res.json();
    return data.install;
}

export async function getInstallProgress(): Promise<InstallJob> {
    const res = await fetch(`${getBaseUrl()}/api/system/install`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Could not read install progress');
    return res.json();
}

// ---------------------------------------------------------------------------
// Phase 4: Batch Queue, Quick-Tools, & MCP Config
// ---------------------------------------------------------------------------

export async function createBatchJob(
    urls: string[],
    options?: CreateProjectOptions,
): Promise<BatchJob> {
    const res = await fetch(`${getBaseUrl()}/api/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            urls,
            clip_min_duration: options?.clip_min_duration ?? 30,
            clip_max_duration: options?.clip_max_duration ?? 60,
            custom_prompt: options?.custom_prompt || null,
            layout_preference: options?.layout_preference ?? 'auto',
            brand_kit_id: options?.brand_kit_id || null,
        }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to start batch');
    }
    return res.json();
}

export async function getBatches(): Promise<BatchJob[]> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/batch`, { cache: 'no-store' });
        if (!res.ok) return [];
        return res.json();
    } catch {
        return [];
    }
}

export async function cancelBatchItem(batchId: string, projectId: string): Promise<void> {
    const res = await fetch(`${getBaseUrl()}/api/batch/${batchId}/cancel/${projectId}`, {
        method: 'POST',
    });
    if (!res.ok) throw new Error('Could not cancel queued item');
}

export async function runQuickTrim(
    file: File,
    start: number,
    end: number,
    accurate: boolean,
): Promise<QuickToolResult> {
    const form = new FormData();
    form.append('file', file);
    form.append('start', String(start));
    form.append('end', String(end));
    form.append('accurate', String(accurate));
    const res = await fetch(`${getBaseUrl()}/api/tools/trim`, { method: 'POST', body: form });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Trim failed');
    }
    return res.json();
}

export async function runQuickCompress(
    file: File,
    preset: string,
    crf?: number,
    maxHeight?: number,
): Promise<QuickToolResult> {
    const form = new FormData();
    form.append('file', file);
    form.append('preset', preset);
    if (crf !== undefined) form.append('crf', String(crf));
    if (maxHeight !== undefined) form.append('max_height', String(maxHeight));
    const res = await fetch(`${getBaseUrl()}/api/tools/compress`, { method: 'POST', body: form });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Compression failed');
    }
    return res.json();
}

export async function runQuickConvert(
    file: File,
    targetFormat: string,
): Promise<QuickToolResult> {
    const form = new FormData();
    form.append('file', file);
    form.append('target_format', targetFormat);
    const res = await fetch(`${getBaseUrl()}/api/tools/convert`, { method: 'POST', body: form });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Conversion failed');
    }
    return res.json();
}

export async function runQuickTransform(
    file: File,
    rotate: number,
    flipH: boolean,
    flipV: boolean,
    aspect: string,
): Promise<QuickToolResult> {
    const form = new FormData();
    form.append('file', file);
    form.append('rotate', String(rotate));
    form.append('flip_h', String(flipH));
    form.append('flip_v', String(flipV));
    form.append('aspect', aspect);
    const res = await fetch(`${getBaseUrl()}/api/tools/transform`, { method: 'POST', body: form });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Transform failed');
    }
    return res.json();
}

export async function getMcpConfig(): Promise<McpConfigInfo | null> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/mcp/config`, { cache: 'no-store' });
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

export async function getCodexStatus(): Promise<CodexStatus | null> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/codex/status`, { cache: 'no-store' });
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

// ---------------------------------------------------------------------------
// Trending Video Discovery & AI Scout
// ---------------------------------------------------------------------------

export async function getDiscoverNiches(): Promise<DiscoverNiche[]> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/discover/niches`);
        if (!res.ok) return [];
        return res.json();
    } catch {
        return [];
    }
}

export async function getTrendingVideos(params: {
    niche?: string;
    query?: string;
    min_minutes?: number;
    limit?: number;
    sort?: 'score' | 'views' | 'duration';
}): Promise<TrendingVideo[]> {
    const qs = new URLSearchParams();
    if (params.niche) qs.set('niche', params.niche);
    if (params.query) qs.set('query', params.query);
    if (params.min_minutes !== undefined) qs.set('min_minutes', String(params.min_minutes));
    if (params.limit !== undefined) qs.set('limit', String(params.limit));
    if (params.sort) qs.set('sort', params.sort);
    const res = await fetch(`${getBaseUrl()}/api/discover/trending?${qs.toString()}`, {
        cache: 'no-store',
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to load trending videos');
    }
    const data = await res.json();
    return data.videos || [];
}

export async function runAiScout(
    videos: TrendingVideo[],
    goal?: string,
): Promise<{ picks: AiScoutPick[]; provider: string }> {
    const res = await fetch(`${getBaseUrl()}/api/discover/ai-scout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ videos, goal: goal || null }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'AI Scout failed');
    }
    return res.json();
}

// ---------------------------------------------------------------------------
// YouTube Shorts OAuth, Auto-Post & Batch Scheduler
// ---------------------------------------------------------------------------

export async function getYouTubeStatus(): Promise<YouTubeStatus | null> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/youtube/status`, { cache: 'no-store' });
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

export async function uploadYouTubeClientSecretFile(file: File): Promise<void> {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${getBaseUrl()}/api/youtube/client-secret/upload`, {
        method: 'POST',
        body: form,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Invalid client_secret.json file');
    }
}

export async function saveYouTubeClientSecretJson(client_secret_json: string): Promise<void> {
    const res = await fetch(`${getBaseUrl()}/api/youtube/client-secret`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_secret_json }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Invalid client secret JSON');
    }
}

export async function getYouTubeAuthUrl(): Promise<{ auth_url: string; redirect_uri: string }> {
    const res = await fetch(`${getBaseUrl()}/api/youtube/auth-url`, { cache: 'no-store' });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Could not start YouTube OAuth');
    }
    return res.json();
}

export async function disconnectYouTube(): Promise<void> {
    await fetch(`${getBaseUrl()}/api/youtube/disconnect`, { method: 'POST' });
}

export async function publishClipToYouTube(
    clipId: string,
    payload: {
        title?: string;
        description?: string;
        tags?: string[];
        privacy_status: 'private' | 'unlisted' | 'public';
        publish_at?: string | null;
        made_for_kids?: boolean;
    },
): Promise<YouTubePublishResult> {
    const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}/publish/youtube`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'YouTube upload failed');
    }
    return res.json();
}

export async function scheduleProjectToYouTube(
    projectId: string,
    payload: {
        start_at: string;
        interval_hours: number;
        description_footer?: string;
        skip_already_posted?: boolean;
        max_uploads?: number;
    },
): Promise<{
    scheduled: (YouTubePublishResult & { clip_id: string; title?: string })[];
    errors: { clip_id: string; title?: string; error: string }[];
    count: number;
}> {
    const res = await fetch(`${getBaseUrl()}/api/projects/${projectId}/schedule-youtube`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'YouTube auto-schedule failed');
    }
    return res.json();
}

// ---------------------------------------------------------------------------
// Whisper Word-Level Audio Re-Sync & Storage Cleanup
// ---------------------------------------------------------------------------

export async function resyncClipWithWhisper(
    clipId: string,
): Promise<{ caption_blocks: CaptionBlock[]; sync_offset_ms: number }> {
    const res = await fetch(`${getBaseUrl()}/api/clips/${clipId}/resync-whisper`, {
        method: 'POST',
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Whisper re-sync failed');
    }
    return res.json();
}

export async function getStorageStats(): Promise<StorageStats | null> {
    try {
        const res = await fetch(`${getBaseUrl()}/api/storage`, { cache: 'no-store' });
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

export async function cleanupStorage(
    target: 'orphans' | 'tools' | 'sources' | 'project_source' | 'failed_projects',
    projectId?: string,
): Promise<{ freed_bytes: number; removed_files: number; stats: StorageStats }> {
    const res = await fetch(`${getBaseUrl()}/api/storage/cleanup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, project_id: projectId || null }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Cleanup failed');
    }
    return res.json();
}
