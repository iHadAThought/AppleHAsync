(() => {
  const TOKEN_KEY = "applehasync_agent_token";

  const $ = (id) => document.getElementById(id);
  const toastEl = $("toast");

  let token = sessionStorage.getItem(TOKEN_KEY) || "";
  let sources = { calendars: [], reminder_lists: [] };
  let haItems = [];

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    toastEl.classList.add("show");
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(() => {
      toastEl.hidden = true;
      toastEl.classList.remove("show");
    }, 2800);
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(path, { ...opts, headers });
    let body = null;
    const text = await res.text();
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { raw: text };
    }
    if (!res.ok) {
      const detail = body && (body.detail || body.error || body.message);
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail || res.status));
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  function setLoggedIn(ok) {
    $("view-login").hidden = ok;
    $("view-app").hidden = !ok;
    $("btn-logout").hidden = !ok;
  }

  function showTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === name);
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.classList.toggle("active", p.id === `tab-${name}`);
    });
  }

  async function refreshHealth() {
    try {
      const h = await fetch("/health").then((r) => r.json());
      const pill = $("health-pill");
      pill.textContent = h.ok ? "Agent OK" : "Agent issue";
      pill.className = `pill ${h.ok ? "ok" : "bad"}`;
      return h;
    } catch {
      $("health-pill").textContent = "Offline";
      $("health-pill").className = "pill bad";
      return null;
    }
  }

  async function tryBootstrap() {
    try {
      const b = await fetch("/v1/setup/bootstrap").then(async (r) => {
        if (!r.ok) return null;
        return r.json();
      });
      if (b && b.agent_token) {
        token = b.agent_token;
        sessionStorage.setItem(TOKEN_KEY, token);
        return b;
      }
    } catch {
      /* remote / non-loopback */
    }
    return null;
  }

  async function loadTokenDisplay() {
    const t = await api("/v1/admin/token");
    $("setup-token").textContent = t.agent_token;
    $("agent-token").textContent = t.agent_token;
  }

  async function loadPerms() {
    const p = await api("/v1/admin/permissions");
    const cal = p.calendar || "?";
    const rem = p.reminders || "?";
    $("perms-status").textContent = `Calendar: ${cal} · Reminders: ${rem}`;
    const ok =
      (cal === "authorized" || cal === "full_access") &&
      (rem === "authorized" || rem === "full_access");
    $("check-perms").className = ok ? "done" : "bad";
    return ok;
  }

  async function loadShares() {
    sources = await api("/v1/admin/sources");
    const calBox = $("calendars-list");
    const listBox = $("lists-list");
    calBox.innerHTML = "";
    listBox.innerHTML = "";
    for (const c of sources.calendars || []) {
      const lab = document.createElement("label");
      lab.innerHTML = `<input type="checkbox" data-kind="calendar" data-id="${c.id}" data-title="${escapeAttr(
        c.title || c.id
      )}" ${c.shared ? "checked" : ""}/><span>${escapeHtml(c.title || c.id)}</span><span class="meta">${escapeHtml(
        c.source_name || ""
      )}</span>`;
      calBox.appendChild(lab);
    }
    for (const l of sources.reminder_lists || []) {
      const lab = document.createElement("label");
      lab.innerHTML = `<input type="checkbox" data-kind="reminder_list" data-id="${l.id}" data-title="${escapeAttr(
        l.title || l.id
      )}" ${l.shared ? "checked" : ""}/><span>${escapeHtml(l.title || l.id)}</span><span class="meta">${escapeHtml(
        l.source_name || ""
      )}</span>`;
      listBox.appendChild(lab);
    }
    const shared =
      (sources.calendars || []).some((c) => c.shared) ||
      (sources.reminder_lists || []).some((l) => l.shared);
    $("check-shares").className = shared ? "done" : "bad";
  }

  async function saveShares() {
    $("shares-err").hidden = true;
    const cals = [];
    const lists = [];
    const calTitles = {};
    const listTitles = {};
    document.querySelectorAll("#calendars-list input:checked").forEach((el) => {
      cals.push(el.dataset.id);
      calTitles[el.dataset.id] = el.dataset.title;
    });
    document.querySelectorAll("#lists-list input:checked").forEach((el) => {
      lists.push(el.dataset.id);
      listTitles[el.dataset.id] = el.dataset.title;
    });
    try {
      await api("/v1/admin/share", {
        method: "PUT",
        body: JSON.stringify({
          shared_calendars: cals,
          shared_reminder_lists: lists,
          calendar_titles: calTitles,
          reminder_titles: listTitles,
        }),
      });
      toast("Shares saved");
      await loadShares();
    } catch (e) {
      $("shares-err").hidden = false;
      $("shares-err").textContent = e.message;
    }
  }

  function renderHa() {
    const box = $("ha-list");
    box.innerHTML = "";
    if (!haItems.length) {
      box.innerHTML = `<p class="muted">No Home Assistant instances yet.</p>`;
      $("check-ha").className = "bad";
      return;
    }
    $("check-ha").className = "done";
    for (const ha of haItems) {
      const card = document.createElement("div");
      card.className = "ha-card";
      card.innerHTML = `
        <header>
          <h3>${escapeHtml(ha.name)}</h3>
          <div class="row">
            <button type="button" class="ghost" data-act="edit" data-key="${escapeAttr(ha.id)}">Edit</button>
            <button type="button" class="ghost" data-act="test" data-key="${escapeAttr(ha.id)}">Test</button>
            <button type="button" class="danger" data-act="del" data-key="${escapeAttr(ha.id)}">Remove</button>
          </div>
        </header>
        <p>${escapeHtml(ha.base_url)}</p>
        <p>webhook: ${escapeHtml(ha.webhook_id || "(none)")} · TLS verify: ${ha.verify_tls ? "on" : "off"}</p>
      `;
      box.appendChild(card);
    }
  }

  async function loadHa() {
    const data = await api("/v1/admin/home-assistants");
    haItems = data.home_assistants || [];
    renderHa();
  }

  async function loadAgentSettings() {
    const s = await api("/v1/admin/settings");
    $("listen-host").value = s.listen_host || "";
    $("listen-port").value = s.listen_port || 8745;
    $("allow-insecure").checked = !!s.allow_insecure_http;
    $("allowed-ips").value = (s.allowed_source_ips || []).join(", ");
  }

  function fillHaForm(ha) {
    $("ha-id").value = ha?.id || "";
    $("ha-name").value = ha?.name || "";
    $("ha-url").value = ha?.base_url || "";
    $("ha-token").value = "";
    $("ha-webhook-id").value = ha?.webhook_id || "";
    $("ha-webhook-secret").value = "";
    $("ha-verify-tls").checked = ha ? !!ha.verify_tls : false;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  async function enterApp(bootstrap) {
    setLoggedIn(true);
    const host = location.hostname;
    const port = location.port || (location.protocol === "https:" ? "443" : "80");
    $("agent-url-hint").textContent = `Agent URL for HA: ${location.protocol}//${host === "127.0.0.1" || host === "localhost" ? "(Mac LAN IP)" : host}:${port}  e.g. https://172.16.1.3:8745`;
    $("header-sub").textContent = bootstrap?.setup_needed
      ? "Finish initial setup"
      : "Settings";
    await refreshHealth();
    await loadTokenDisplay();
    await loadPerms();
    await loadShares();
    await loadHa();
    await loadAgentSettings();
    if (bootstrap?.setup_needed) showTab("setup");
  }

  async function init() {
    await refreshHealth();
    const boot = await tryBootstrap();
    if (boot?.agent_token) {
      await enterApp(boot);
      return;
    }
    if (token) {
      try {
        await api("/v1/admin/settings");
        await enterApp({ setup_needed: false });
        return;
      } catch {
        sessionStorage.removeItem(TOKEN_KEY);
        token = "";
      }
    }
    setLoggedIn(false);
  }

  // Events
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => showTab(btn.dataset.tab));
  });

  $("btn-login").addEventListener("click", async () => {
    $("login-err").hidden = true;
    token = $("login-token").value.trim();
    if (!token) {
      $("login-err").hidden = false;
      $("login-err").textContent = "Token required";
      return;
    }
    try {
      await api("/v1/admin/settings");
      sessionStorage.setItem(TOKEN_KEY, token);
      await enterApp({ setup_needed: false });
    } catch (e) {
      $("login-err").hidden = false;
      $("login-err").textContent = e.message || "Invalid token";
      token = "";
    }
  });

  $("btn-logout").addEventListener("click", () => {
    sessionStorage.removeItem(TOKEN_KEY);
    token = "";
    setLoggedIn(false);
  });

  $("btn-request-perms").addEventListener("click", async () => {
    await api("/v1/admin/permissions", {
      method: "POST",
      body: JSON.stringify({ action: "request", which: "both" }),
    });
    toast("Permission request sent");
    await loadPerms();
  });

  $("btn-open-privacy").addEventListener("click", async () => {
    await api("/v1/admin/permissions", {
      method: "POST",
      body: JSON.stringify({ action: "open_settings", which: "both" }),
    });
    toast("Opened Privacy settings");
  });

  $("btn-copy-token").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("setup-token").textContent);
    toast("Token copied");
  });
  $("btn-copy-agent-token").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("agent-token").textContent);
    toast("Token copied");
  });

  $("btn-finish-setup").addEventListener("click", async () => {
    await api("/v1/admin/settings", {
      method: "PUT",
      body: JSON.stringify({ setup_completed: true }),
    });
    toast("Setup marked complete");
    $("header-sub").textContent = "Settings";
    showTab("ha");
  });

  $("btn-refresh-shares").addEventListener("click", () => loadShares().then(() => toast("Refreshed")));
  $("btn-save-shares").addEventListener("click", saveShares);

  $("ha-list").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const key = btn.dataset.key;
    const act = btn.dataset.act;
    if (act === "edit") {
      const ha = haItems.find((h) => h.id === key);
      fillHaForm(ha);
      return;
    }
    if (act === "test") {
      try {
        const r = await api(`/v1/admin/home-assistants/${encodeURIComponent(key)}/test`, {
          method: "POST",
          body: "{}",
        });
        toast(r.ok ? "HA connection OK" : `HA test failed: ${r.status_code || r.error || "error"}`);
      } catch (e) {
        toast(e.message);
      }
      return;
    }
    if (act === "del") {
      if (!confirm("Remove this Home Assistant registration?")) return;
      await api(`/v1/admin/home-assistants/${encodeURIComponent(key)}`, { method: "DELETE" });
      toast("Removed");
      await loadHa();
    }
  });

  $("btn-ha-reset").addEventListener("click", () => fillHaForm(null));

  $("btn-ha-test-form").addEventListener("click", async () => {
    $("ha-err").hidden = true;
    const id = $("ha-id").value;
    const url = $("ha-url").value.trim();
    const tok = $("ha-token").value;
    try {
      let r;
      if (id && !tok) {
        r = await api(`/v1/admin/home-assistants/${encodeURIComponent(id)}/test`, {
          method: "POST",
          body: "{}",
        });
      } else {
        if (!url || !tok) {
          throw new Error("URL and HA token required to test (or Test a saved instance)");
        }
        r = await api("/v1/admin/home-assistants/test", {
          method: "POST",
          body: JSON.stringify({
            base_url: url,
            token: tok,
            verify_tls: $("ha-verify-tls").checked,
          }),
        });
      }
      toast(r.ok ? "HA connection OK" : `HA test failed: ${r.status_code || r.error || "error"}`);
    } catch (e) {
      $("ha-err").hidden = false;
      $("ha-err").textContent = e.message;
    }
  });

  $("ha-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("ha-err").hidden = true;
    const id = $("ha-id").value;
    const payload = {
      name: $("ha-name").value.trim(),
      base_url: $("ha-url").value.trim(),
      webhook_id: $("ha-webhook-id").value.trim(),
      webhook_secret: $("ha-webhook-secret").value,
      verify_tls: $("ha-verify-tls").checked,
      enabled: true,
    };
    const tok = $("ha-token").value;
    if (tok) payload.token = tok;
    try {
      if (id) {
        await api(`/v1/admin/home-assistants/${encodeURIComponent(id)}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        toast("Updated");
      } else {
        if (!payload.token) throw new Error("HA token required for new instance");
        const created = await api("/v1/admin/home-assistants", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        $("ha-id").value = created.id;
        toast("Saved");
      }
      await loadHa();
    } catch (e) {
      $("ha-err").hidden = false;
      $("ha-err").textContent = e.message;
    }
  });

  $("agent-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("agent-err").hidden = true;
    const ips = $("allowed-ips")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const r = await api("/v1/admin/settings", {
        method: "PUT",
        body: JSON.stringify({
          listen_host: $("listen-host").value.trim(),
          listen_port: Number($("listen-port").value),
          allow_insecure_http: $("allow-insecure").checked,
          allowed_source_ips: ips,
        }),
      });
      toast(r.restart_needed ? "Saved — restart LaunchAgent to apply listen/TLS" : "Saved");
    } catch (e) {
      $("agent-err").hidden = false;
      $("agent-err").textContent = e.message;
    }
  });

  $("btn-rotate-token").addEventListener("click", async () => {
    if (!confirm("Rotate agent token? Update the HA integration afterward.")) return;
    const r = await api("/v1/admin/token/rotate", { method: "POST", body: "{}" });
    token = r.agent_token;
    sessionStorage.setItem(TOKEN_KEY, token);
    $("setup-token").textContent = token;
    $("agent-token").textContent = token;
    toast("Token rotated");
  });

  init();
})();
