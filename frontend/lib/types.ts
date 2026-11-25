export type CaptionAnimation =
    | 'none'
    | 'word_by_word'
    | 'highlight'
    | 'one_word'
    | 'chase'
    | 'sweep'
    | 'stamp'
    | 'jelly'
    | 'glow';

export type LayoutMode =
    | 'auto'
    | 'dynamic_zoom'
    | 'podcast_split'
    | 'podcast_switch'
    | 'tutorial'
    | 'original';

export interface CaptionWord {
    id: string;
    word: string;
    start: number;
    end: number;
    deleted?: boolean;
    is_filler?: boolean;
}

export interface CaptionBlock {
    id: string;
    start: number;
    end: number;
    text: string;
    emoji?: string;
    words: CaptionWord[];
}

export interface CutSegment {
    id: string;
    start: number;
    end: number;
    reason?: 'filler' | 'silence' | 'manual';
}

export interface CaptionOverrides {
    font: string;
    fontsize: number;
    primary_color: string;
    highlight_color: string;
    outline_color: string;
    outline_width: number;
    shadow: number;
    background: boolean;
    bg_color: string;
    animation: CaptionAnimation;
    position_y: number;
    uppercase: boolean;
    max_words_per_line: number;
    letter_spacing: number;
    sync_offset_ms?: number;
}

export interface LogoConfig {
    enabled: boolean;
    asset_url: string | null;
    x: number;
    y: number;
    scale: number;
    opacity: number;
}

export interface ClipEditState {
    trim_start: number;
    trim_end: number;
    cuts: CutSegment[];
    layout_mode: LayoutMode | string;
    caption_enabled: boolean;
    caption_style: string;
    caption_overrides: CaptionOverrides;
    caption_blocks: CaptionBlock[];
    logo: LogoConfig;
    brand_kit_id?: string | null;
}

export interface Project {
    id: string;
    youtube_url: string;
    title: string;
    status:
        | 'pending'
        | 'downloading'
        | 'transcribing'
        | 'analyzing'
        | 'processing'
        | 'done'
        | 'error';
    source_type?: 'youtube' | 'upload';
    clip_min_duration?: number;
    clip_max_duration?: number;
    custom_prompt?: string | null;
    layout_preference?: LayoutMode | string;
    brand_kit_id?: string | null;
    created_at: string;
    error_message?: string | null;
    clip_count?: number;
}

export interface Clip {
    id: string;
    project_id: string;
    file_path: string;
    clean_file_path?: string | null;
    start_time: number;
    end_time: number;
    duration: number;
    reframed: boolean;
    captioned: boolean;
    title?: string;
    reason?: string;
    viral_score?: number;
    face_count?: number;
    layout_mode?: string;
    caption_style?: string;
    needs_user_confirm?: boolean;
    hashtags?: string[];
    tags?: string[];
    edit_state?: ClipEditState | null;
    youtube_video_id?: string | null;
    youtube_url?: string | null;
    youtube_status?: string | null;
    youtube_scheduled_at?: string | null;
    created_at?: string;
}

export interface BrandKit {
    id: string;
    name: string;
    logo_path?: string | null;
    caption_style: string;
    caption_overrides: Partial<CaptionOverrides>;
    logo_config: Partial<LogoConfig>;
    created_at?: string;
}

export interface ProgressUpdate {
    stage: string;
    percent: number;
    message: string;
}

export interface Settings {
    llm_provider: 'codex' | 'openai' | 'anthropic' | 'gemini' | 'ollama';
    llm_model: string;
    whisper_model: 'base' | 'small' | 'medium';
    has_api_key: boolean;
    caption_style: string;
}

export interface CodexStatus {
    installed: boolean;
    version: string | null;
    logged_in: boolean;
    status_text: string;
}

export interface CaptionStyle {
    key: string;
    name: string;
    category?: 'kinetic' | 'clean' | 'vibrant';
    animation: CaptionAnimation;
    config?: Partial<CaptionOverrides>;
    preview_colors: {
        text: string;
        highlight: string | null;
        background: string | null;
    };
}

export interface ClipSuggestion {
    start: number;
    end: number;
    title: string;
    reason: string;
}

export interface SystemTool {
    id: 'ffmpeg' | 'yt_dlp' | 'python_packages';
    name: string;
    description: string;
    ok: boolean;
    can_install: boolean;
    manual: string;
    missing?: string[];
}

export interface InstallStep {
    id: SystemTool['id'];
    name: string;
    state: 'pending' | 'running' | 'done' | 'error';
    progress: number;
    detail: string;
}

export interface InstallJob {
    state: 'idle' | 'running' | 'done' | 'error';
    percent: number;
    message: string;
    steps: InstallStep[];
    error: string | null;
    log: string[];
}

export interface SystemHealth {
    ready: boolean;
    platform: 'windows' | 'macos' | 'linux';
    tools: SystemTool[];
    install: InstallJob;
}

export interface BatchItem {
    project_id: string;
    youtube_url: string;
    title?: string | null;
    status: string;
    clip_count: number;
    error_message?: string | null;
}

export interface BatchJob {
    id: string;
    created_at: number;
    status: 'running' | 'done' | 'error';
    total: number;
    completed: number;
    failed: number;
    queued: number;
    active: number;
    clip_min_duration: number;
    clip_max_duration: number;
    layout_preference: string;
    custom_prompt?: string | null;
    items: BatchItem[];
}

export interface MediaProbeInfo {
    size_bytes: number;
    duration: number;
    width: number;
    height: number;
    video_codec?: string | null;
    audio_codec?: string | null;
}

export interface QuickToolResult {
    download_url: string;
    filename: string;
    format?: string;
    savings_percent?: number;
    input: MediaProbeInfo;
    output: MediaProbeInfo;
}

export interface McpConfigInfo {
    python_path: string;
    script_path: string;
    config_json: string;
    codex_toml?: string;
    tools: { name: string; description: string }[];
}

export interface DiscoverNiche {
    key: string;
    name: string;
    query: string;
    default_layout: string;
}

export interface TrendingVideo {
    id: string;
    title: string;
    url: string;
    channel: string;
    channel_verified: boolean;
    duration: number;
    view_count: number;
    thumbnail: string;
    clip_score: number;
    signals: string[];
    recommended_layout: string;
}

export interface AiScoutPick {
    video_id: string;
    ai_score: number;
    hook_angle: string;
    suggested_prompt: string;
    recommended_layout: string;
}

export interface YouTubeStatus {
    has_client_secret: boolean;
    connected: boolean;
    channel_title?: string | null;
    channel_id?: string | null;
    channel_thumbnail?: string | null;
    redirect_uri: string;
    error?: string | null;
}

export interface YouTubePublishResult {
    video_id: string;
    url: string;
    studio_url: string;
    status: string;
    publish_at?: string | null;
}

export interface StorageProjectItem {
    id: string;
    title: string;
    status: string;
    source_type: string;
    source_bytes: number;
    clips_bytes: number;
    orphan_bytes?: number;
    total_bytes: number;
    has_source: boolean;
    clip_count: number;
}

export interface StorageStats {
    total_bytes: number;
    source_videos_bytes: number;
    source_videos_count: number;
    clips_bytes: number;
    clips_count: number;
    orphan_bytes: number;
    orphan_count: number;
    tools_bytes: number;
    tools_count: number;
    failed_projects_bytes: number;
    failed_projects_count: number;
    projects: StorageProjectItem[];
}
