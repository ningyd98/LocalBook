export const enUS = {
  // App title
  appTitle: "LocalNote Server",
  appSubtitle: "Markdown-first local workspace",

  // Ribbon
  ribbon: {
    files: "Files",
    search: "Search",
    graph: "Graph",
    ai: "AI",
    history: "History",
    settings: "Settings",
  },

  // Status cards
  status: {
    server: "Server",
    vault: "Vault",
    ai: "AI",
    connected: "Connected",
    disconnected: "Disconnected",
    checking: "Checking…",
    notConfigured: "Not configured",
    unavailable: "Unavailable",
    offline: "Offline",
    error: "Error",
    refreshStatus: "Refresh status",
  },

  // File tree
  fileTree: {
    retry: "Retry",
    noFiles: "No files",
  },

  // Tabs
  tabs: {
    close: "Close",
    retry: "Retry",
    unsavedChanges: "Discard unsaved changes?",
  },

  // Save state
  saveState: {
    saved: "saved",
    saving: "saving",
    conflict: "conflict",
    error: "error",
    loading: "loading",
  },

  // Editor
  // Attachment uploads
  attachment: {
    insert: "Insert attachment",
    upload: "Upload attachment",
    uploading: "Uploading…",
    uploaded: "Upload complete",
    uploadFailed: "Upload failed",
    fileTooLarge: "File exceeds the size limit",
    alreadyExists: "A file with that name already exists; a new name was used",
    invalidDirectory: "Invalid target folder",
    directoryNotFound: "Target folder does not exist; create it first",
    dropHere: "Drop here",
    pasteImage: "Paste an image",
    chooseFile: "Choose a file",
    uploadToDirectory: "Upload to this folder",
    uploadToRoot: "Upload to vault root",
    open: "Open attachment",
    download: "Download",
    preview: "Attachment preview",
    previewFailed: "Preview failed to load",
    noEditableNote: "Open an editable Markdown note first",
    loading: "Loading…",
  },

  editor: {
    readOnly: "This file is not valid UTF-8 and is read-only.",
    themeLight: "Light theme",
    themeDark: "Dark theme",
  },

  // Heading toolbar
  heading: {
    toolbar: "Heading format",
    h1: "Heading 1",
    h2: "Heading 2",
    h3: "Heading 3",
    h4: "Heading 4",
    h5: "Heading 5",
    h6: "Heading 6",
    plain: "Body text (clear heading)",
  },

  // Conflict
  conflict: {
    title: "External modification detected.",
    description: "Reload to discard local changes, or keep local to stop autosave.",
    reload: "Reload (discard local)",
    keepLocal: "Keep local",
  },

  // Workspace
  workspace: {
    openFilePrompt: "Open a Markdown file to start editing.",
  },

  // Links
  links: {
    outgoing: "Outgoing Links",
    backlinks: "Backlinks",
    broken: "broken",
    retry: "Retry",
  },

  // Search
  search: {
    title: "Search Notes",
    placeholder: "Enter search keywords…",
    searching: "Searching…",
    noResults: "No results found",
    results: "results",
    close: "Close",
  },

  // Graph
  graph: {
    title: "Knowledge Graph",
    scope: "Scope",
    scopeGlobal: "Global",
    scopeLocal: "Local",
    scopeTag: "Tag",
    depth: "Depth",
    direction: "Direction",
    directionBoth: "Both",
    directionOutgoing: "Outgoing",
    directionIncoming: "Incoming",
    includeBroken: "Include broken links",
    tagFilter: "Tag filter",
    noteFilter: "Note filter",
    close: "Close",
    loading: "Loading…",
    noNodes: "No nodes",
  },

  // AI
  ai: {
    title: "AI Assistant",
    ask: "Ask",
    summarize: "Summarize",
    tags: "Generate Tags",
    related: "Related Notes",
    extractTodos: "Extract Todos",
    classify: "Classify",
    questionPlaceholder: "Enter your question…",
    send: "Send",
    working: "Working…",
    offline: "AI service offline",
    close: "Close",
  },

  // History
  history: {
    title: "AI History",
    noJobs: "No history yet",
    close: "Close",
  },

  // Settings
  settings: {
    title: "Settings",
    close: "Close",
    
    general: "General",
    language: "Language",
    theme: "Theme",
    themeLight: "Light",
    themeDark: "Dark",
    themeAuto: "Auto",
    
    vault: "Vault",
    vaultPath: "Vault Path",
    vaultPathPlaceholder: "Not configured",
    vaultPathHelp: "Root directory for Markdown files",
    changeVault: "Change Path",
    
    editor: "Editor",
    autoSave: "Auto Save",
    autoSaveDelay: "Auto Save Delay",
    autoSaveDelayHelp: "Time to wait after typing stops (milliseconds)",
    fontSize: "Font Size",
    lineHeight: "Line Height",
    
    aiSettings: "AI Configuration",
    aiEnabled: "Enable AI",
    aiEndpoint: "AI Endpoint",
    aiEndpointPlaceholder: "http://127.0.0.1:1234/v1",
    aiModel: "Model",
    aiModelPlaceholder: "Qwen3.5-4B",
    
    advanced: "Advanced",
    devMode: "Developer Mode",
    debugLogs: "Debug Logs",
    clearCache: "Clear Cache",
    rebuildIndex: "Rebuild Index",
    rebuildIndexConfirm: "Are you sure you want to rebuild the index? This may take some time.",
    
    about: "About",
    version: "Version",
    documentation: "Documentation",
    reportIssue: "Report Issue",
  },

  // Common buttons
  buttons: {
    save: "Save",
    cancel: "Cancel",
    confirm: "Confirm",
    delete: "Delete",
    edit: "Edit",
    close: "Close",
    back: "Back",
    next: "Next",
    apply: "Apply",
    reset: "Reset",
  },
};

export type TranslationKeys = typeof enUS;
