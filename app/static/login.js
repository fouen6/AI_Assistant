import { ApiClient, explain } from './api.js';
import { AuthGate, Brand, Icon, roleLabel } from './ui.js';
let api;
window.Vue.createApp({
  components: { AuthGate, Brand, Icon },
  data: () => ({ user: null, busy: false, info: '', infoIsError: false, epoch: 0 }),
  methods: {
    roleLabel,
    async submit(credentials, register = false) {
      if (this.busy) return;
      this.busy = true; this.info = ''; this.infoIsError = false; const ticket = this.epoch;
      try {
        if (register) {
          await api.register(credentials.username, credentials.password);
          if (ticket === this.epoch) this.info = '普通员工账号已创建。请切换到“登录”并使用刚才的账号。';
        } else {
          const user = await api.login(credentials.username, credentials.password);
          if (ticket === this.epoch) this.user = user;
        }
      } catch (error) { if (ticket === this.epoch) { this.info = explain(error); this.infoIsError = true; } }
      finally { if (ticket === this.epoch) this.busy = false; }
    },
    login(credentials) { return this.submit(credentials); },
    register(credentials) { return this.submit(credentials, true); },
    async refreshIdentity() {
      if (this.busy) return;
      this.busy = true; const ticket = this.epoch;
      try { const user = await api.me(); if (ticket === this.epoch) { this.user = user; this.info = '身份已重新读取。'; this.infoIsError = false; } }
      catch (error) { if (ticket === this.epoch) { this.info = explain(error); this.infoIsError = true; } }
      finally { if (ticket === this.epoch) this.busy = false; }
    },
    logout() { api.clear(); this.epoch++; this.user = null; this.info = ''; this.busy = false; }
  },
  created() { api = new ApiClient(() => { this.logout(); this.info = '登录已失效，请重新登录。'; this.infoIsError = true; }); }
}).mount('#app');
