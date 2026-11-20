import { ApiClient, explain } from './api.js';
import { pending, remember, forget, newPayload } from './operations.js';
import { Icon, Brand, AuthGate, Modal, StatusPill, shortId, formatDate, roleLabel } from './ui.js';

const AVATARS = { person: '👤', robot: '🤖', cat: '🐱', plant: '🌱' };
let api;
window.Vue.createApp({
  components: { Icon, Brand, AuthGate, Modal, StatusPill },
  data: () => ({
    user: null, busy: false, loading: false, error: '', sessions: [], sid: '', snapshot: null,
    newMode: 'chat', message: '', epoch: 0, localPending: null, retryPayload: null,
    stream: true, preview: '', sendingText: '', aborter: null, transportInfo: '', avatarKey: 'person',
    avatarDraft: 'person', avatarChoices: AVATARS, avatarLabels: { person: '人物头像', robot: '机器人头像', cat: '小猫头像', plant: '植物头像' },
    sidebarOpen: false, dialog: '', sessionQuery: '', titles: {}, copied: '', toast: '', toastTimer: null,
    suggestions: [
      { title: '查找制度依据', desc: '年假、补贴与考勤，找到原文再回答。', icon: 'book', mode: 'agent', text: '请查询员工手册：住房补贴每月多少钱，入职多久可以申请？请给出原文出处。' },
      { title: '让计算更轻松', desc: '把具体计算交给工具，核对执行结果。', icon: 'bolt', mode: 'agent', text: '请使用 multiply 计算 3500 乘以 12，收到工具结果后再回答。' },
      { title: '一起理清思路', desc: '解释一个概念，或聊聊你的新想法。', icon: 'chat', mode: 'chat', text: '请用一个简单的例子解释，AI Agent 是怎样使用工具完成任务的？' }
    ]
  }),
  computed: {
    avatar() { return AVATARS[this.avatarKey] || AVATARS.person; },
    currentSession() { return this.sessions.find(s => s.id === this.sid); },
    currentMode() { return this.currentSession?.mode || this.newMode; },
    modeLabel() { return this.currentMode === 'agent' ? '智能体' : '普通对话'; },
    filteredSessions() {
      const query = this.sessionQuery.trim().toLowerCase();
      return [...this.sessions].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
        .filter(s => !query || [this.sessionTitle(s), s.id, s.mode, s.mode === 'agent' ? '智能体' : '普通对话'].join(' ').toLowerCase().includes(query));
    },
    visibleMessages() {
      return (this.snapshot?.messages || []).filter(m => m.role === 'human' ||
        (m.role === 'ai' && !(m.tool_calls || []).length && (m.display_content ?? m.content)))
        .map(m => ({ ...m, content: m.display_content ?? m.content }));
    },
    cannotSend() {
      return this.busy || this.loading || !this.sid || !this.message.trim() || !!this.localPending ||
        !this.snapshot || ['awaiting_approval', 'incomplete'].includes(this.snapshot.status) || this.snapshot.write_blocked;
    },
    composerPlaceholder() {
      if (!this.sid) return '先开启一段新对话，或选择上方的任务卡片…';
      if (this.snapshot?.status === 'awaiting_approval') return '通知等待管理员审批，审批后刷新即可继续。';
      return this.currentMode === 'agent' ? '问问公司制度，或描述一个需要工具协助的任务…' : '写下你的问题，让我们一起理清思路…';
    }
  },
  methods: {
    shortId, formatDate, roleLabel,
    sessionTitle(s) { return this.titles[s.id] || (s.mode === 'agent' ? '智能体任务' : '普通对话') + ' · ' + shortId(s.id); },
    reset() {
      this.epoch++; this.user = null; this.sessions = []; this.sid = ''; this.snapshot = null;
      this.localPending = null; this.retryPayload = null; this.message = ''; this.preview = ''; this.sendingText = '';
      this.avatarKey = 'person'; this.busy = false; this.loading = false; this.aborter = null;
      this.transportInfo = ''; this.error = ''; this.sidebarOpen = false; this.dialog = '';
      this.sessionQuery = ''; this.titles = {}; this.toast = ''; clearTimeout(this.toastTimer);
    },
    logout() { this.aborter?.abort(); api.clear(); this.reset(); },
    async login(credentials) {
      if (this.busy) return;
      this.busy = true; this.error = ''; const ticket = this.epoch;
      try {
        const user = await api.login(credentials.username, credentials.password);
        const sessions = await api.sessions(), profile = await api.profile();
        if (ticket !== this.epoch) return;
        this.user = user; this.sessions = sessions; this.avatarKey = profile.avatar_key;
      } catch (error) { if (ticket === this.epoch) this.error = explain(error); }
      finally { if (ticket === this.epoch) this.busy = false; }
    },
    openNew() { this.newMode = this.currentMode || 'chat'; this.dialog = 'new'; this.sidebarOpen = false; },
    async createSession() {
      if (this.busy) return;
      this.busy = true; this.error = ''; const ticket = this.epoch;
      let activeEpoch = ticket;
      try {
        const row = await api.createSession(this.newMode), sessions = await api.sessions();
        if (ticket !== this.epoch) return;
        this.sessions = sessions.some(s => s.id === row.id) ? sessions : [...sessions, row];
        this.dialog = '';
        const selection = this.selectSession(row.id);
        activeEpoch = this.epoch;
        await selection;
      } catch (error) { if (activeEpoch === this.epoch) this.error = explain(error); }
      finally { if (activeEpoch === this.epoch) this.busy = false; }
    },
    async selectSession(sid) {
      this.epoch++; this.sid = sid; this.snapshot = null; this.preview = ''; this.error = '';
      this.transportInfo = ''; this.message = ''; this.retryPayload = null; this.sidebarOpen = false;
      this.localPending = sid && this.user ? pending(this.user.id, sid) : null;
      if (sid) { await this.refresh(); this.scrollBottom(true); }
    },
    async refresh() {
      if (!this.sid || !this.user) return;
      const ticket = this.epoch, sid = this.sid; this.loading = true;
      try {
        const state = await api.snapshot(sid);
        if (ticket !== this.epoch || sid !== this.sid) return;
        this.snapshot = state;
        const first = (state.messages || []).find(m => m.role === 'human');
        if (first) this.titles[sid] = String(first.content).replace(/\s+/g, ' ').slice(0, 40);
      } catch (error) { if (ticket === this.epoch) this.error = explain(error); }
      finally { if (ticket === this.epoch) this.loading = false; }
    },
    async useSuggestion(suggestion) {
      if (this.busy || this.loading) return;
      if (!this.sid || this.currentMode !== suggestion.mode || this.snapshot?.status === 'awaiting_approval' || this.snapshot?.status === 'incomplete' || this.snapshot?.write_blocked) {
        this.newMode = suggestion.mode; await this.createSession();
      }
      if (!this.sid || this.localPending || this.busy) return;
      this.message = suggestion.text;
      this.$nextTick(() => this.$refs.composer?.focus());
    },
    composerKeydown(event) {
      // 中文输入法确认候选词时不能意外发送。
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault(); this.send();
      }
    },
    async send() {
      if (this.cannotSend) return;
      this.retryPayload = newPayload(this.message);
      this.localPending = remember(this.user.id, this.sid, this.retryPayload.request_id, 'turn');
      await this.submit(this.retryPayload);
    },
    async retry() { if (!this.busy && this.retryPayload && this.localPending) await this.submit(this.retryPayload); },
    async submit(payload) {
      const ticket = this.epoch, sid = this.sid, owner = this.user.id;
      this.busy = true; this.error = ''; this.preview = ''; this.transportInfo = ''; this.sendingText = payload.message;
      this.aborter = new AbortController();
      try {
        if (this.stream) {
          const { postTurnStream } = await import('./stream-client.js');
          await postTurnStream(api, sid, payload, event => {
            if (ticket !== this.epoch) return;
            if (event.event === 'draft_start') this.preview = '';
            if (event.event === 'delta') { this.preview += event.data.text; this.scrollBottom(); }
            if (event.event === 'preview_limit') this.transportInfo = '草稿预览已达上限，正在等待最终结果。';
          }, this.aborter.signal);
        } else { await api.turn(sid, payload); }
        if (ticket !== this.epoch) return;
        forget(owner, sid); this.localPending = null; this.retryPayload = null; this.message = '';
        this.sendingText = ''; await this.refresh(); this.scrollBottom(true);
      } catch (error) {
        if (ticket !== this.epoch) return;
        this.error = error.name === 'AbortError'
          ? '已停止接收。服务端可能仍在处理，请先查询原请求的结果。' : explain(error);
        await this.refresh();
      } finally {
        if (ticket === this.epoch) { this.busy = false; this.aborter = null; this.preview = ''; this.sendingText = ''; }
      }
    },
    async reconcile() {
      if (!this.localPending || !this.user || this.busy) return;
      const ticket = this.epoch, meta = this.localPending; this.busy = true; this.error = '';
      try {
        const row = await api.operation(meta.sid, meta.request_id);
        if (ticket !== this.epoch) return;
        this.transportInfo = row.status === 'completed' ? '原请求已完成，已重新读取当前会话。' : '原请求状态：' + row.status + '，请稍后再查询。';
        if (row.status === 'completed') {
          forget(this.user.id, meta.sid); this.localPending = null; this.retryPayload = null; this.message = '';
        }
        // 操作缓存可能早于管理员恢复；当前会话始终单独只读查询。
        await this.refresh();
      } catch (error) {
        if (ticket === this.epoch) { this.error = explain(error) + ' 查询不到记录也不能更换请求 ID 自动重发。'; await this.refresh(); }
      } finally { if (ticket === this.epoch) this.busy = false; }
    },
    scrollBottom(force = false) {
      const el = this.$refs.scroll;
      if (force || !el || el.scrollHeight - el.scrollTop - el.clientHeight < 140)
        this.$nextTick(() => { if (this.$refs.scroll) this.$refs.scroll.scrollTop = this.$refs.scroll.scrollHeight; });
    },
    openProfile() { this.avatarDraft = this.avatarKey; this.dialog = 'profile'; },
    async saveAvatar() {
      if (this.busy) return;
      const ticket = this.epoch; this.busy = true; this.error = '';
      try {
        const profile = await api.avatar(this.avatarDraft);
        if (ticket !== this.epoch) return;
        this.avatarKey = profile.avatar_key; this.dialog = ''; this.showToast('头像已更新');
      } catch (error) { if (ticket === this.epoch) { this.dialog = ''; this.error = explain(error); } }
      finally { if (ticket === this.epoch) this.busy = false; }
    },
    showToast(text) { this.toast = text; clearTimeout(this.toastTimer); this.toastTimer = setTimeout(() => { this.toast = ''; }, 2200); },
    async copyMessage(message) {
      try { await navigator.clipboard.writeText(message.content); this.copied = message.id; this.showToast('回答已复制'); }
      catch { this.showToast('无法自动复制，请选择回答文字复制。'); }
    }
  },
  created() { api = new ApiClient(() => { this.reset(); this.error = '登录已失效，请重新登录。'; }); }
}).mount('#app');
