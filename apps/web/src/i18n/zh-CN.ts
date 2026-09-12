export const zhCN = {
  // 应用标题
  appTitle: "本地笔记服务器",
  appSubtitle: "Markdown 优先的本地工作空间",

  // 工具栏
  ribbon: {
    files: "文件",
    search: "搜索",
    graph: "图谱",
    ai: "AI助手",
    history: "历史记录",
    settings: "设置",
  },

  // 状态卡片
  status: {
    server: "服务器",
    vault: "笔记库",
    ai: "AI",
    connected: "已连接",
    disconnected: "断开连接",
    checking: "检查中…",
    notConfigured: "未配置",
    unavailable: "不可用",
    offline: "离线",
    error: "错误",
    refreshStatus: "刷新状态",
  },

  // 文件树
  fileTree: {
    retry: "重试",
    noFiles: "没有文件",
  },

  // 标签栏
  tabs: {
    close: "关闭",
    retry: "重试保存",
    unsavedChanges: "放弃未保存的更改？",
  },

  // 保存状态
  saveState: {
    saved: "已保存",
    saving: "保存中",
    conflict: "冲突",
    error: "错误",
    loading: "加载中",
  },

  // 编辑器
  // 附件上传
  attachment: {
    insert: "插入附件",
    upload: "上传附件",
    uploading: "上传中…",
    uploaded: "上传成功",
    uploadFailed: "上传失败",
    fileTooLarge: "文件超过大小限制",
    alreadyExists: "同名文件已存在，已自动使用新名称",
    invalidDirectory: "目标目录无效",
    directoryNotFound: "目标目录不存在，请先创建",
    dropHere: "拖到此处",
    pasteImage: "粘贴图片",
    chooseFile: "选择文件",
    uploadToDirectory: "上传到该目录",
    uploadToRoot: "上传到笔记库根目录",
    open: "打开附件",
    download: "下载",
    preview: "附件预览",
    previewFailed: "预览加载失败",
    noEditableNote: "请先打开一个可编辑的 Markdown 笔记",
    loading: "正在加载…",
  },

  editor: {
    readOnly: "此文件不是有效的 UTF-8 编码，只能只读。",
    themeLight: "浅色主题",
    themeDark: "深色主题",
  },

  // 标题工具条
  heading: {
    toolbar: "标题格式",
    h1: "一级标题",
    h2: "二级标题",
    h3: "三级标题",
    h4: "四级标题",
    h5: "五级标题",
    h6: "六级标题",
    plain: "正文（取消标题）",
  },

  // 冲突提示
  conflict: {
    title: "检测到外部修改。",
    description: "重新加载会丢弃本地更改，或保留本地更改以停止自动保存。",
    reload: "重新加载（放弃本地）",
    keepLocal: "保留本地",
  },

  // 工作区状态
  workspace: {
    openFilePrompt: "打开一个 Markdown 文件开始编辑。",
  },

  // 链接面板
  links: {
    outgoing: "引用链接",
    backlinks: "反向链接",
    broken: "损坏",
    retry: "重试",
  },

  // 搜索面板
  search: {
    title: "搜索笔记",
    placeholder: "输入搜索关键词…",
    searching: "搜索中…",
    noResults: "没有找到结果",
    results: "条结果",
    close: "关闭",
  },

  // 图谱面板
  graph: {
    title: "知识图谱",
    scope: "范围",
    scopeGlobal: "全局",
    scopeLocal: "局部",
    scopeTag: "标签",
    depth: "深度",
    direction: "方向",
    directionBoth: "双向",
    directionOutgoing: "引用",
    directionIncoming: "反向引用",
    includeBroken: "包含损坏链接",
    tagFilter: "标签筛选",
    noteFilter: "笔记筛选",
    close: "关闭",
    loading: "加载中…",
    noNodes: "没有节点",
  },

  // AI 面板
  ai: {
    title: "AI 助手",
    ask: "提问",
    summarize: "总结",
    tags: "生成标签",
    related: "相关笔记",
    extractTodos: "提取待办",
    classify: "分类",
    questionPlaceholder: "输入你的问题…",
    send: "发送",
    working: "处理中…",
    offline: "AI 服务离线",
    close: "关闭",
  },

  // 历史记录面板
  history: {
    title: "AI 历史记录",
    noJobs: "暂无历史记录",
    close: "关闭",
  },

  // 设置页面
  settings: {
    title: "设置",
    close: "关闭",
    
    // 通用设置
    general: "通用",
    language: "语言",
    theme: "主题",
    themeLight: "浅色",
    themeDark: "深色",
    themeAuto: "跟随系统",
    
    // Vault 设置
    vault: "笔记库",
    vaultPath: "笔记库路径",
    vaultPathPlaceholder: "未配置",
    vaultPathHelp: "Markdown 文件存储的根目录",
    changeVault: "更改路径",
    
    // 编辑器设置
    editor: "编辑器",
    autoSave: "自动保存",
    autoSaveDelay: "自动保存延迟",
    autoSaveDelayHelp: "输入停止后多久自动保存（毫秒）",
    fontFamily: "字体",
    fontFamilyHelp: "界面与笔记使用的字体组合",
    fontTarget: "应用范围",
    fontTargetAll: "整个界面",
    fontTargetNote: "仅笔记正文",
    fontScale: "字号缩放",
    fontScaleHelp: "整体放大或缩小界面与笔记（80%–160%）",
    fontScaleReset: "恢复默认",
    fontSans: "无衬线",
    fontSerif: "衬线",
    fontMono: "等宽",
    fontRounded: "圆体",
    fontSize: "字体大小",
    lineHeight: "行高",
    
    // AI 设置
    aiSettings: "AI 配置",
    aiEnabled: "启用 AI",
    aiEndpoint: "AI 服务端点",
    aiEndpointPlaceholder: "http://127.0.0.1:1234/v1",
    aiModel: "模型",
    aiModelPlaceholder: "Qwen3.5-4B",
    
    // 高级设置
    advanced: "高级",
    devMode: "开发者模式",
    debugLogs: "调试日志",
    clearCache: "清除缓存",
    rebuildIndex: "重建索引",
    rebuildIndexConfirm: "确定要重建索引吗？这可能需要一些时间。",
    
    // 关于
    about: "关于",
    version: "版本",
    documentation: "文档",
    reportIssue: "报告问题",
  },

  // 通用按钮
  buttons: {
    save: "保存",
    cancel: "取消",
    confirm: "确认",
    delete: "删除",
    edit: "编辑",
    close: "关闭",
    back: "返回",
    next: "下一步",
    apply: "应用",
    reset: "重置",
  },
};

export type TranslationKeys = typeof zhCN;
