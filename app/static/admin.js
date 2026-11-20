import { ApiClient, ApiError, explain } from './api.js';
import { allPending, remember, forget } from './operations.js';
import { Icon, Brand, AuthGate, Modal, StatusPill, shortId, formatDate, formatSize, roleLabel } from './ui.js';
const AVATARS = { person: '👤', robot: '🤖', cat: '🐱', plant: '🌱' };
let api;
window.Vue.createApp({
  components: { Icon, Brand, AuthGate, Modal, StatusPill },
  data: () => ({
    user: null, busy: false, error: '', info: '', epoch: 0,
    tab: 'approvals', sidebarOpen: false, query: '', dialog: '', review: null, deleting: null, editing: null,
    filter: 'pending', rows: [], state: null, localPending: null, lastDecision: null,
    documents: [], file: null, uploading: false, dragging: false, users: [],
    avatarKey: 'person', avatarDraft: 'person', avatarChoices: AVATARS,
    avatarLabels: { person: '人物头像', robot: '机器人头像', cat: '小猫头像', plant: '植物头像' },
    approvalFilters: [{ value: 'pending', label: '待审批' }, { value: 'approved', label: '已批准' }, { value: 'rejected', label: '已拒绝' }, { value: '', label: '全部记录' }],
    navigation: [
      { key: 'approvals', icon: 'shield', label: '审批中心', english: 'APPROVAL CENTER', description: '每一项通知操作，在执行前得到认真确认。' },
      { key: 'documents', icon: 'book', label: '知识文档', english: 'KNOWLEDGE LIBRARY', description: '把分散的工作资料，变成可查询的企业知识。' },
      { key: 'users', icon: 'users', label: '账号管理', english: 'PEOPLE & ACCESS', description: '清晰的角色，合适的权限，有序的协作。' }
    ]
  }),
  computed: {
    avatar() { return AVATARS[this.avatarKey] || AVATARS.person; },
    currentPage() { return this.navigation.find(n => n.key === this.tab); },
    visibleApprovals() {
      const query = this.query.trim().toLowerCase();
      return this.rows.filter(r => !query || [r.args_json?.recipient, r.args_json?.content, r.session_id, r.tool_name].join(' ').toLowerCase().includes(query));
    },
    visibleDocuments() {
      const query = this.query.trim().toLowerCase();
      return this.documents.filter(d => !query || [d.filename, d.id].join(' ').toLowerCase().includes(query));
    },
    visibleUsers() { return this.users.filter(u => u.username.toLowerCase().includes(this.query.trim().toLowerCase())); },
    chunkCount() { return this.documents.reduce((sum, d) => sum + Number(d.chunk_count || 0), 0); }
  },
  methods: {
    shortId, formatDate, formatSize, roleLabel,
    reset() {
      this.epoch++; this.user = null; this.rows = []; this.state = null; this.documents = []; this.users = [];
      this.localPending = null; this.lastDecision = null; this.file = null; this.busy = false;
      this.uploading = false; this.dragging = false; this.avatarKey = 'person';
      this.info = ''; this.error = ''; this.sidebarOpen = false; this.query = '';
      this.dialog = ''; this.review = null; this.editing = null; this.deleting = null; this.filter = 'pending';
    },
    logout() { api.clear(); this.reset(); },
    async run(action) {
      if (this.busy) return false;
      const ticket = this.epoch; this.busy = true; this.error = ''; this.info = '';
      try { await action(); return ticket === this.epoch; }
      catch (error) { if (ticket === this.epoch) this.error = explain(error); return false; }
      finally { if (ticket === this.epoch) this.busy = false; }
    },
    async login(credentials) {
      await this.run(async () => {
        const user = await api.login(credentials.username, credentials.password);
        if (user.role !== 'admin') {
          api.clear(); throw new ApiError(403, 'admin_required', '当前账号没有管理员权限，请前往助手首页。');
        }
        // 读取完成后才进入工作台，避免错误登录时出现残留管理数据。
        const rows = await api.approvals(this.filter), documents = await api.documents();
        const users = await api.users(), profile = await api.profile();
        this.user = user; this.rows = rows; this.documents = documents; this.users = users;
        this.avatarKey = profile.avatar_key;
        this.localPending = allPending(user.id).find(m => m.kind === 'resume') || null;
      });
    },
    switchTab(tab) { if (!this.busy) { this.tab = tab; this.query = ''; this.error = ''; this.info = ''; this.sidebarOpen = false; } },
    reloadTab() { return this.tab === 'approvals' ? this.loadApprovals() : this.tab === 'documents' ? this.loadDocuments() : this.loadUsers(); },
    async setFilter(value) { if (!this.busy) { this.filter = value; this.query = ''; await this.loadApprovals(); } },
    async loadApprovals() { await this.run(async () => { this.rows = await api.approvals(this.filter); }); },
    closeDialog() { if (!this.busy) { this.dialog = ''; this.review = null; this.deleting = null; this.editing = null; } },
    async decide(row, approved) {
      if (this.busy || this.localPending || row.decision_status !== 'pending') return;
      this.lastDecision = { sid: row.session_id, iid: row.interrupt_id, body: { request_id: crypto.randomUUID(), approved } };
      this.localPending = remember(this.user.id, row.session_id, this.lastDecision.body.request_id, 'resume');
      await this.repeatDecision();
    },
    async repeatDecision() {
      if (!this.lastDecision) return;
      await this.run(async () => {
        const { sid, iid, body } = this.lastDecision;
        await api.resume(sid, iid, body);
        // 旧请求缓存不是当前会话，审批后重新只读获取最新状态。
        this.state = await api.snapshot(sid, true);
        forget(this.user.id, sid); this.localPending = null;
        this.rows = await api.approvals(this.filter);
        this.dialog = ''; this.review = null;
        this.info = '审批处理已返回结果。可在下方核对最新会话与工具执行情况。';
      });
    },
    async reconcile() {
      if (!this.localPending) return;
      await this.run(async () => {
        const m = this.localPending, row = await api.operation(m.sid, m.request_id, true);
        this.state = await api.snapshot(m.sid, true);
        this.info = row.status === 'completed' ? '原审批请求已完成，已读取最新会话。' : '审批操作状态：' + row.status + '，请稍后再查询。';
        if (row.status === 'completed') {
          forget(this.user.id, m.sid); this.localPending = null;
          this.rows = await api.approvals(this.filter); this.dialog = ''; this.review = null;
        }
      });
    },
    async loadDocuments() { await this.run(async () => { this.documents = await api.documents(); }); },
    chooseFile(file) {
      if (this.busy) return;
      this.error = ''; this.info = ''; this.file = null;
      if (!file) return;
      if (!/\.(txt|md)$/i.test(file.name)) { this.error = '请选择 .txt 或 .md 文档。'; return; }
      if (file.size > 5 * 1024 * 1024) { this.error = '文件超过 5 MB，请先缩小文件。'; return; }
      if (!file.size) { this.error = '文件内容为空，请选择有正文的资料。'; return; }
      this.file = file;
    },
    dropFile(event) { this.dragging = false; if (!this.busy) this.chooseFile(event.dataTransfer.files[0]); },
    async upload() {
      if (!this.file || this.busy) return;
      this.uploading = true;
      try {
        await this.run(async () => {
          const row = await api.upload(this.file);
          this.documents = await api.documents(); this.file = null;
          if (this.$refs.file) this.$refs.file.value = '';
          this.info = '“' + row.filename + '”已入库，可在新一轮知识查询中使用。';
        });
      } finally { this.uploading = false; }
    },
    async removeDocument() {
      if (!this.deleting) return;
      const row = this.deleting;
      await this.run(async () => {
        await api.removeDocument(row.id); this.documents = await api.documents();
        this.dialog = ''; this.deleting = null;
        this.info = '“' + row.filename + '”已从当前知识库删除，既有会话历史保留。';
      });
    },
    async loadUsers() { await this.run(async () => { this.users = await api.users(); }); },
    editUser(row) { this.error = ''; this.editing = { ...row, next_role: row.role, next_active: row.is_active }; this.dialog = 'user'; },
    async saveUser() {
      if (!this.editing) return;
      const row = this.editing;
      if (row.next_role === row.role && row.next_active === row.is_active) { this.closeDialog(); return; }
      await this.run(async () => {
        const changed = await api.changeUser(row.id, row.next_role, row.next_active);
        const me = await api.me(); this.user = me;
        if (me.role !== 'admin') { api.clear(); this.reset(); throw new ApiError(403, 'admin_required', '当前角色不再允许继续管理。'); }
        this.users = await api.users(); this.dialog = ''; this.editing = null;
        this.info = '“' + changed.username + '”的账号设置已更新。';
      });
    },
    openProfile() { this.avatarDraft = this.avatarKey; this.dialog = 'profile'; },
    async saveAvatar() {
      await this.run(async () => { this.avatarKey = (await api.avatar(this.avatarDraft)).avatar_key; this.dialog = ''; this.info = '头像已更新。'; });
    }
  },
  created() { api = new ApiClient(() => { this.reset(); this.error = '登录已失效，请重新登录。'; }); }
}).mount('#app');
