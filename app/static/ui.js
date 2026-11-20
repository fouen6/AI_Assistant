// 页面共用的图标、对话框和展示函数；不处理身份、网络请求或业务执行。
const paths = {
  brand: ['M12 2.5 14.5 9.5 21.5 12 14.5 14.5 12 21.5 9.5 14.5 2.5 12 9.5 9.5Z'],
  chat: ['M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l-3 3V11.5A7.5 7.5 0 0 1 9.5 4h3a7.5 7.5 0 0 1 7.5 7.5Z', 'M7 10h8M7 14h5'],
  plus: ['M12 5v14M5 12h14'],
  search: ['M16.5 16.5 21 21', 'M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z'],
  arrow: ['M5 12h14M13 6l6 6-6 6'],
  send: ['M12 19V5M6 11l6-6 6 6'],
  chevron: ['m8 10 4 4 4-4'],
  back: ['M19 12H5m6-6-6 6 6 6'],
  file: ['M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z', 'M14 3v6h6M8 13h8M8 17h5'],
  book: ['M12 5v15M3 4h5a4 4 0 0 1 4 2 4 4 0 0 1 4-2h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3Z'],
  shield: ['M12 3 3.5 6.5V12c0 5 5 8 8.5 9 3.5-1 8.5-4 8.5-9V6.5Z', 'm8 12 3 3 5-6'],
  users: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M17 4a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.9', 'M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z'],
  user: ['M20 21v-2a5 5 0 0 0-5-5H9a5 5 0 0 0-5 5v2', 'M16 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z'],
  logout: ['M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M10 12h11m-4-4 4 4-4 4'],
  settings: ['M4 7h16M4 17h16M8 4v6M16 14v6'],
  copy: ['M9 9h11v11H9Z', 'M15 5V3H3v12h2'],
  check: ['m5 12 4 4L19 6'],
  x: ['m6 6 12 12M18 6 6 18'],
  clock: ['M12 7v5l3 2', 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z'],
  refresh: ['M20 7v5h-5M4 17v-5h5', 'M5.3 7a8 8 0 0 1 13.2-2L20 7M4 17l1.5 2A8 8 0 0 0 18.7 17'],
  upload: ['M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5'],
  trash: ['M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7'],
  menu: ['M4 6h16M4 12h16M4 18h16'],
  bolt: ['m13 2-9 12h7l-1 8 10-12h-7Z'],
  info: ['M12 11v6M12 7h.01', 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z'],
  eye: ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z', 'M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'],
  lock: ['M5 10h14v11H5ZM8 10V6a4 4 0 0 1 8 0v4'],
  mail: ['M3 5h18v14H3ZM3 6l9 7 9-7'],
  down: ['M12 5v14m-6-6 6 6 6-6'],
  square: ['M6 6h12v12H6Z'],
  layers: ['m12 3 10 6-10 6L2 9ZM3 14l9 5 9-5M3 18l9 5 9-5'],
  code: ['m8 6-6 6 6 6M16 6l6 6-6 6M14 3l-4 18'],
};

export const Icon = {
  props: { name: { type: String, default: 'chat' }, size: { type: [Number, String], default: 20 } },
  computed: { shapes() { return paths[this.name] || paths.chat; } },
  template: `
<svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
<path v-for="(d,i) in shapes" :key="i" :d="d"/>
</svg>
`
};

export const Brand = {
  components: { Icon },
  props: { compact: Boolean, dark: Boolean },
  template: `
<div class="brand" :class="{'brand-on-dark':dark}">
<span class="brand-mark">
<Icon name="brand" :size="25"/>
</span>
<span class="brand-wordmark">企业智能助手<span v-if="!compact" class="brand-caption">ENTERPRISE AI</span>
</span>
</div>
`
};

export const AuthGate = {
  components: { Icon, Brand },
  props: { busy: Boolean, error: String, admin: Boolean, register: Boolean, info: String },
  emits: ['login', 'register'],
  data: () => ({ username: '', password: '', showPassword: false, tab: 'login' }),
  methods: {
    submit() {
      if (this.busy) return;
      this.$emit(this.tab === 'register' ? 'register' : 'login', { username: this.username, password: this.password });
      this.password = '';
    }
  },
  template: `
<div class="auth-screen">
<aside class="auth-aside">
<Brand/>
<div class="auth-story">
<div class="eyebrow" style="margin-bottom:18px">A LITTLE CLARITY. EVERY DAY.</div>
<h1>让繁杂的工作，<br>变得<em>清晰有序。</em>
</h1>
<p>从一条提问开始，连接知识、工具与协作。<br>把时间留给真正重要的事。</p>
<div class="auth-illustration" aria-label="工作方式示意">
<div class="illustration-header">
<i class="window-dot">
</i>
<i class="window-dot">
</i>
<i class="window-dot">
</i>
<span class="eyebrow">工作方式示意</span>
</div>
<div class="illustration-message">帮我找到制度依据，再完成计算。</div>
<div class="illustration-answer">
<span class="brand-mark">
<Icon name="brand" :size="19"/>
</span>
<div>
<p>查找相关资料，核对原文出处，<br>使用工具完成下一步。</p>
<span class="icon-label">
<Icon name="check" :size="12"/>每一步，都有据可查</span>
</div>
</div>
</div>
<div class="auth-capabilities">
<span>
<Icon name="book" :size="14"/>企业知识</span>
<span>
<Icon name="bolt" :size="14"/>工具协作</span>
<span>
<Icon name="shield" :size="14"/>人工审批</span>
</div>
</div>
<div class="auth-footer">企业智能助手 · 为专注工作而设计</div>
</aside>
<main class="auth-main">
<div class="auth-card">
<div class="auth-mobile-brand">
<Brand/>
</div>
<div class="eyebrow">{{admin?'ADMIN CONSOLE':'YOUR AI WORKSPACE'}}</div>
<h2>{{tab==='register'?'创建你的账号':admin?'进入管理工作台':'欢迎回来'}}</h2>
<p class="auth-description">{{tab==='register'?'注册普通员工账号，开始使用企业智能助手。':admin?'审批、知识与成员，在这里有序管理。':'登录企业智能助手，让每一次提问都更进一步。'}}</p>
<div v-if="register" class="auth-tabs" role="tablist" aria-label="登录或注册">
<button type="button" role="tab" :aria-selected="tab==='login'" :class="{selected:tab==='login'}" @click="tab='login'">登录</button>
<button type="button" role="tab" :aria-selected="tab==='register'" :class="{selected:tab==='register'}" @click="tab='register'">注册</button>
</div>
<form class="auth-form" @submit.prevent="submit">
<div v-if="error" class="notice notice-error" role="alert">{{error}}</div>
<div v-if="info" class="notice notice-success" role="status">{{info}}</div>
<label class="field">
<span>用户名</span>
<div class="input-with-icon">
<Icon class="icon" name="user" :size="17"/>
<input v-model="username" class="input" name="username" autocomplete="username" placeholder="请输入用户名" required :disabled="busy"/>
</div>
</label>
<label class="field">
<span>密码</span>
<div class="password-input input-with-icon">
<Icon class="icon" name="lock" :size="17"/>
<input v-model="password" class="input" name="password" aria-label="密码" :type="showPassword?'text':'password'" :autocomplete="tab==='register'?'new-password':'current-password'" placeholder="请输入密码" required :disabled="busy"/>
<button type="button" class="icon-button" :aria-label="showPassword?'隐藏密码':'显示密码'" :aria-pressed="showPassword" @click="showPassword=!showPassword">
<Icon name="eye" :size="17"/>
</button>
</div>
<small v-if="tab==='register'" class="field-note">密码 12–128 位；用户名使用小写字母、数字或下划线。</small>
</label>
<button class="button button-primary button-full" :disabled="busy">
<span v-if="busy" class="spinner">
</span>{{busy?'正在处理…':tab==='register'?'创建账号':'登 录'}}<Icon v-if="!busy" name="arrow" :size="16"/>
</button>
</form>
<p v-if="!register&&!admin" class="auth-footnote">还没有账号？ <a href="/auth-lab">注册普通员工</a>
</p>
<p v-if="admin" class="auth-footnote">
<Icon name="shield" :size="13"/> 仅限拥有管理员权限的账号</p>
<div class="auth-context">
<Icon name="lock" :size="13"/>登录身份仅保留在当前页面，刷新后需重新登录。</div>
<p class="auth-back">
<a :href="admin||register?'/':'/admin'">{{admin||register?'返回助手首页':'管理员工作台'}} <span aria-hidden="true">↗</span>
</a>
</p>
</div>
</main>
</div>
`
};

const statuses = {
  done: ['已完成', 'green'], empty: ['待开始', 'slate'], awaiting_approval: ['等待审批', 'amber'],
  incomplete: ['未完成', 'amber'], pending: ['待审批', 'amber'], approved: ['已批准', 'green'],
  rejected: ['已拒绝', 'red'], not_started: ['未执行', 'slate'], succeeded: ['执行成功', 'green'],
  failed: ['执行失败', 'red'], running: ['处理中', 'violet'], uncertain: ['待核对', 'amber'],
  evidence_checked: ['依据已核对', 'blue'], insufficient: ['资料不足', 'slate'],
  verified_tool_result: ['结果已确认', 'green'], needs_review: ['回答需核对', 'amber'],
  model_text: ['普通回答', 'slate'], success: ['成功', 'green'], error: ['失败', 'red']
};
export function statusLabel(value) { return statuses[value]?.[0] || value || '—'; }
export const StatusPill = {
  props: { value: String },
  computed: { description() { return statuses[this.value] || [this.value || '—', 'slate']; } },
  template: `
<span class="status-pill" :class="'pill-'+description[1]">
<span class="status-dot">
</span>{{description[0]}}</span>
`
};

export const Modal = {
  components: { Icon }, props: { title: String, wide: Boolean }, emits: ['close'],
  mounted() { this.$refs.dialog.showModal(); },
  beforeUnmount() { this.$refs.dialog?.close(); },
  template: `
<dialog ref="dialog" class="app-modal" :class="{'modal-wide':wide}" :aria-label="title" @cancel.prevent="$emit('close')" @click="($event.target === $refs.dialog) && $emit('close')">
<div class="modal-panel">
<header class="modal-header">
<h2>{{title}}</h2>
<button class="icon-button" type="button" aria-label="关闭对话框" @click="$emit('close')">
<Icon name="x"/>
</button>
</header>
<div class="modal-content">
<slot/>
</div>
<div v-if="$slots.footer" class="modal-footer">
<slot name="footer"/>
</div>
</div>
</dialog>
`
};

export function shortId(value) { const s = String(value || ''); return s.length > 15 ? s.slice(0, 8) + '…' + s.slice(-4) : s || '—'; }
export function formatDate(value, full = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', { ...(full ? { year: 'numeric' } : {}), month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}
export function formatSize(value) { return value < 1024 ? value + ' B' : (value / 1024).toFixed(1) + ' KB'; }
export function roleLabel(role) { return role === 'admin' ? '管理员' : '普通员工'; }
