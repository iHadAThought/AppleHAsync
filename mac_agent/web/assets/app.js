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

  const CAL_FIELD_LABELS = {
    notes: "Notes",
    location: "Location / address",
    url: "URL / link",
  };
  const REM_FIELD_LABELS = {
    notes: "Notes",
    due: "Due date / time",
    priority: "Priority / urgent",
    flagged: "Flag",
    location: "Location",
    url: "URL / link",
    tags: "Tags",
  };

  function fieldChecksHtml(kind, id, syncFields, labels) {
    const keys = Object.keys(labels);
    const bits = keys
      .map((key) => {
        const checked = syncFields && syncFields[key] !== false ? "checked" : "";
        return `<label class="field-chip"><input type="checkbox" data-field-kind="${kind}" data-source-id="${escapeAttr(
          id
        )}" data-field="${key}" ${checked}/><span>${escapeHtml(labels[key])}</span></label>`;
      })
      .join("");
    return `<div class="field-row" data-fields-for="${escapeAttr(id)}">${bits}</div>`;
  }

  function renderSourceRow(kind, item, labels) {
    const wrap = document.createElement("div");
    wrap.className = "source-card";
    wrap.dataset.id = item.id;
    wrap.dataset.kind = kind;
    const sync = item.sync_fields || {};
    wrap.innerHTML = `
      <label class="source-enable">
        <input type="checkbox" data-kind="${kind}" data-id="${escapeAttr(item.id)}" data-title="${escapeAttr(
      item.title || item.id
    )}" ${item.shared ? "checked" : ""}/>
        <span class="source-title">${escapeHtml(item.title || item.id)}</span>
        <span class="meta">${escapeHtml(item.source_name || "")}</span>
      </label>
      <p class="field-caption">Details to sync</p>
      ${fieldChecksHtml(kind, item.id, sync, labels)}
    `;
    const enable = wrap.querySelector(".source-enable input");
    const fieldRow = wrap.querySelector(".field-row");
    const syncEnable = () => {
      fieldRow.classList.toggle("disabled", !enable.checked);
      fieldRow.querySelectorAll("input").forEach((inp) => {
        inp.disabled = !enable.checked;
      });
    };
    enable.addEventListener("change", syncEnable);
    syncEnable();
    return wrap;
  }

  async function loadShares() {
    sources = await api("/v1/admin/sources");
    const calBox = $("calendars-list");
    const listBox = $("lists-list");
    calBox.innerHTML = "";
    listBox.innerHTML = "";
    for (const c of sources.calendars || []) {
      calBox.appendChild(renderSourceRow("calendar", c, CAL_FIELD_LABELS));
    }
    for (const l of sources.reminder_lists || []) {
      listBox.appendChild(renderSourceRow("reminder_list", l, REM_FIELD_LABELS));
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
    const calendarSyncFields = {};
    const reminderSyncFields = {};

    document.querySelectorAll("#calendars-list .source-card").forEach((card) => {
      const enable = card.querySelector(".source-enable input");
      const id = enable.dataset.id;
      if (enable.checked) {
        cals.push(id);
        calTitles[id] = enable.dataset.title;
      }
      const fields = {};
      card.querySelectorAll(".field-row input[data-field]").forEach((inp) => {
        fields[inp.dataset.field] = !!inp.checked;
      });
      calendarSyncFields[id] = fields;
    });
    document.querySelectorAll("#lists-list .source-card").forEach((card) => {
      const enable = card.querySelector(".source-enable input");
      const id = enable.dataset.id;
      if (enable.checked) {
        lists.push(id);
        listTitles[id] = enable.dataset.title;
      }
      const fields = {};
      card.querySelectorAll(".field-row input[data-field]").forEach((inp) => {
        fields[inp.dataset.field] = !!inp.checked;
      });
      reminderSyncFields[id] = fields;
    });

    try {
      await api("/v1/admin/share", {
        method: "PUT",
        body: JSON.stringify({
          shared_calendars: cals,
          shared_reminder_lists: lists,
          calendar_titles: calTitles,
          reminder_titles: listTitles,
          calendar_sync_fields: calendarSyncFields,
          reminder_sync_fields: reminderSyncFields,
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
