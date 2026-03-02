// ─── Constants ────────────────────────────────────────────────────────────────

const STAGE_LABELS = {
  queued: "Queued",
  converting: "Converting",
  transcribing: "Transcribing",
};

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
];

// ─── State ────────────────────────────────────────────────────────────────────

const state = {
  // Recording
  mediaRecorder: null,
  recordChunks: [],
  recordStream: null,
  recordStart: null,
  timerInterval: null,
  analyser: null,
  animFrame: null,
  appendMode: false,
  appendJobId: null,

  // Voice edit
  voiceEditMode: false,
  voiceEditChunks: [],
  voiceEditRecorder: null,
  voiceEditStream: null,

  // Jobs
  activeJobId: null,
  jobElements: new Map(),
  jobStartTime: {},
  lastUpdateTime: {},
  lastProgress: {},
  elapsedInterval: null,
  aiTriggeredJobs: new Set(),

  // Reader for any active streaming AI request (so we can cancel it)
  currentAIReader: null,

  // Prefs
  autoFixEnabled: localStorage.getItem("autoFixEnabled") === "true",

  // AI modes (populated from server)
  aiModes: {},
  aiModeOrder: [],
  currentAIMode: null,

  // Auto-fix tracking to prevent multiple runs
  autoFixRunning: false,
  autoFixCompleted: new Set(),
};
