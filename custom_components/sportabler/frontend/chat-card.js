/* Read-only Abler conversation card for Home Assistant dashboards. */
const STYLE = `
  :host { display:block; color:var(--primary-text-color,#242933); font-family:var(--paper-font-body1_-_font-family,Arial,sans-serif); }
  ha-card { display:block; overflow:hidden; border-radius:20px; background:var(--ha-card-background,var(--card-background-color,#fff)); }
  button { font:inherit; cursor:pointer; }
  .top { display:flex; align-items:center; gap:12px; min-height:64px; padding:12px 18px; border-bottom:1px solid var(--divider-color,#e9ebf0); }
  .toptext { flex:1; min-width:0; }
  .title { font-size:19px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .subtitle { font-size:12px; color:var(--secondary-text-color,#8992a0); margin-top:2px; }
  .icon { border:0; background:transparent; color:var(--primary-text-color,#242933); width:36px; height:36px; border-radius:18px; font-size:24px; display:grid; place-items:center; }
  .icon:hover, .thread:hover { background:var(--secondary-background-color,#f4f5f9); }
  .icon:focus-visible, .thread:focus-visible, .older:focus-visible { outline:2px solid var(--primary-color,#1685c2); outline-offset:2px; }
  .inbox { max-height:610px; overflow-y:auto; }
  .thread { width:100%; display:flex; align-items:center; gap:12px; padding:13px 18px; border:0; border-bottom:1px solid var(--divider-color,#f0f1f4); text-align:left; color:inherit; background:transparent; }
  .avatar { flex:0 0 40px; height:40px; border-radius:50%; display:grid; place-items:center; color:var(--primary-text-color,#343d48); background:var(--secondary-background-color,#f0f2f8); font-size:13px; font-weight:700; }
  .thread-main { min-width:0; flex:1; }
  .thread-line { display:flex; gap:8px; align-items:baseline; }
  .thread-name { flex:1; min-width:0; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .thread-time { color:var(--secondary-text-color,#9aa2ae); font-size:11px; white-space:nowrap; }
  .preview { color:var(--secondary-text-color,#7b8491); font-size:13px; margin-top:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .badge { flex:0 0 auto; background:var(--primary-color,#1685c2); color:#fff; border-radius:14px; min-width:18px; height:18px; display:grid; place-items:center; font-size:11px; padding:0 4px; }
  .chat { height:min(70vh,620px); min-height:330px; overflow-y:auto; padding:16px 18px 12px; scroll-behavior:smooth; }
  .day { text-align:center; color:var(--secondary-text-color,#9ba5b3); font-size:11px; text-transform:uppercase; margin:22px 0 18px; letter-spacing:.04em; }
  .message-row { display:flex; align-items:end; gap:10px; margin:10px 0; }
  .message-row.mine { justify-content:flex-end; }
  .message-row.mine .avatar { display:none; }
  .message-content { max-width:min(82%,540px); min-width:0; }
  .sender { font-size:12px; color:var(--secondary-text-color,#7f8793); margin:0 0 4px 3px; }
  .bubble { background:var(--secondary-background-color,#f2f3fa); padding:10px 14px; border-radius:18px 18px 18px 5px; white-space:pre-wrap; overflow-wrap:anywhere; font-size:15px; line-height:1.42; }
  .mine .bubble { background:color-mix(in srgb,var(--primary-color,#1685c2) 16%,var(--ha-card-background,#fff)); border-radius:18px 18px 5px 18px; }
  .mine .sender { text-align:right; }
  .time { color:var(--secondary-text-color,#9ba5b3); font-size:10px; margin:4px 3px 0; }
  .mine .time { text-align:right; }
  .attachment { display:block; margin-top:8px; font-size:12px; color:var(--primary-color,#1685c2); }
  .status { color:var(--secondary-text-color,#88919d); text-align:center; padding:28px 18px; font-size:14px; }
  .older { display:block; margin:0 auto 8px; border:0; color:var(--primary-color,#1685c2); background:transparent; padding:6px 12px; }
  .footer { border-top:1px solid var(--divider-color,#e9ebf0); padding:12px 18px; color:var(--secondary-text-color,#929ba8); font-size:12px; text-align:center; }
  @media (max-width:600px) { .top { padding:10px 14px; } .chat { padding:12px 12px 8px; } .message-content { max-width:83%; } }
`;

class SportablerChatCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._messages = [];
    this._selected = null;
    this._loading = false;
    this._error = null;
    this._pageInfo = null;
    this._remotePageInfo = null;
    this._remoteCursor = null;
    this._requestId = 0;
  }

  setConfig(config) {
    if (!config.entity || typeof config.entity !== "string") {
      throw new Error("Set entity to the Sportabler Conversations sensor");
    }
    this._config = config;
    this._render();
  }

  set hass(value) {
    const previous = this._hass?.states?.[this._config?.entity];
    const previousCount = previous?.attributes?.stored_message_count;
    this._hass = value;
    const current = value?.states?.[this._config?.entity];
    if (current !== previous) {
      this._render();
      if (this._selected && current?.attributes?.stored_message_count !== previousCount) {
        this._loadMessages(true);
      }
    }
  }

  connectedCallback() { this._render(); }
  getCardSize() { return 8; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: 8, min_rows: 5 }; }

  _state() { return this._hass?.states?.[this._config?.entity]; }
  _conversations() {
    const items = this._state()?.attributes?.conversations;
    return Array.isArray(items) ? items : [];
  }
  _selectedConversation() { return this._conversations().find((item) => item.id === this._selected); }
  _name(value) { return value || "Unknown sender"; }
  _initials(value) {
    return this._name(value).trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
  }
  _date(value, options) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value || "";
    return new Intl.DateTimeFormat(this._hass?.locale?.language || navigator.language, options).format(date);
  }
  _element(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  async _loadMessages(reset) {
    if (!this._hass || !this._selected || this._loading) return;
    const entryId = this._state()?.attributes?.entry_id;
    if (!entryId) {
      this._error = "Update the Sportabler integration to use this chat card.";
      this._render();
      return;
    }
    const selected = this._selected;
    const requestId = ++this._requestId;
    const after = reset ? undefined : this._pageInfo?.endCursor;
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const result = await this._hass.callWS({
        type: "sportabler/stored_messages",
        entry_id: entryId,
        conversation_id: selected,
        first: 30,
        ...(after ? { after } : {}),
      });
      if (selected !== this._selected || requestId !== this._requestId) return;
      const existing = this._messages;
      const byId = new Map(existing.map((item) => [String(item.id), item]));
      for (const item of result.items || []) byId.set(String(item.id), item);
      this._messages = Array.from(byId.values()).sort((a, b) =>
        String(b.createdAt || "").localeCompare(String(a.createdAt || ""))
      );
      this._pageInfo = result.page_info || null;
    } catch (error) {
      if (selected === this._selected) this._error = error?.message || "Could not load stored messages.";
    } finally {
      if (selected === this._selected && requestId === this._requestId) {
        this._loading = false;
        this._render();
        if (reset) requestAnimationFrame(() => {
          const chat = this.shadowRoot.querySelector(".chat");
          if (chat) chat.scrollTop = chat.scrollHeight;
        });
      }
    }
  }

  async _importOlder() {
    if (!this._hass || !this._selected || this._loading) return;
    const selected = this._selected;
    const entryId = this._state()?.attributes?.entry_id;
    const requestId = ++this._requestId;
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const result = await this._hass.callWS({
        type: "sportabler/import_messages",
        entry_id: entryId,
        conversation_id: selected,
        first: 30,
        ...(this._remoteCursor ? { after: this._remoteCursor } : {}),
      });
      if (selected !== this._selected || requestId !== this._requestId) return;
      const byId = new Map(this._messages.map((item) => [String(item.id), item]));
      for (const item of result.items || []) byId.set(String(item.id), item);
      this._messages = Array.from(byId.values()).sort((a, b) =>
        String(b.createdAt || "").localeCompare(String(a.createdAt || ""))
      );
      this._remotePageInfo = result.page_info || null;
      this._remoteCursor = this._remotePageInfo?.endCursor || null;
    } catch (error) {
      if (selected === this._selected) this._error = error?.message || "Could not load older messages.";
    } finally {
      if (selected === this._selected && requestId === this._requestId) {
        this._loading = false;
        this._render();
      }
    }
  }

  _open(id) {
    this._selected = id;
    this._messages = [];
    this._pageInfo = null;
    this._remotePageInfo = null;
    this._remoteCursor = null;
    this._error = null;
    this._render();
    this._loadMessages(true);
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="top"></div><div class="body"></div></ha-card>`;
    const top = this.shadowRoot.querySelector(".top");
    const body = this.shadowRoot.querySelector(".body");
    const selected = this._selectedConversation();
    if (this._selected && !selected) this._selected = null;
    if (this._selected) {
      const back = this._element("button", "icon", "‹");
      back.type = "button";
      back.setAttribute("aria-label", "Back to conversations");
      back.addEventListener("click", () => { this._selected = null; this._requestId++; this._render(); });
      top.append(back);
    }
    const titleBox = this._element("div", "toptext");
    titleBox.append(this._element("div", "title", this._selected ? selected?.name : (this._config.title || "Abler messages")));
    titleBox.append(this._element("div", "subtitle", this._selected ? "Stored messages · read-only" : `${this._conversations().length} conversations`));
    top.append(titleBox);
    const refresh = this._element("button", "icon", "↻");
    refresh.type = "button";
    refresh.setAttribute("aria-label", "Refresh inbox from Abler");
    refresh.title = "Refresh inbox from Abler";
    refresh.addEventListener("click", () => this._hass?.callService("homeassistant", "update_entity", { entity_id: this._config.entity }));
    top.append(refresh);
    if (this._selected) this._renderThread(body);
    else this._renderInbox(body);
  }

  _renderInbox(body) {
    const list = this._element("div", "inbox");
    const threads = this._conversations().slice().sort((a, b) =>
      String(b.latest_message?.created_at || "").localeCompare(String(a.latest_message?.created_at || ""))
    );
    if (!threads.length) {
      list.append(this._element("div", "status", "No conversations loaded yet. Use ↻ to refresh."));
    }
    for (const conversation of threads) {
      const row = this._element("button", "thread");
      row.type = "button";
      row.addEventListener("click", () => this._open(conversation.id));
      row.append(this._element("div", "avatar", this._initials(conversation.name)));
      const main = this._element("div", "thread-main");
      const line = this._element("div", "thread-line");
      line.append(this._element("div", "thread-name", conversation.name || "Conversation"));
      line.append(this._element("div", "thread-time", this._date(conversation.latest_message?.created_at, { day:"numeric", month:"short" })));
      main.append(line);
      main.append(this._element("div", "preview", conversation.latest_message?.body || "No text message"));
      row.append(main);
      if (conversation.unread_count > 0) row.append(this._element("span", "badge", conversation.unread_count));
      list.append(row);
    }
    body.append(list);
  }

  _renderThread(body) {
    const chat = this._element("div", "chat");
    if (this._pageInfo?.hasNextPage) {
      const older = this._element("button", "older", this._loading ? "Loading…" : "Load older messages");
      older.type = "button";
      older.disabled = this._loading;
      older.addEventListener("click", () => this._loadMessages(false));
      chat.append(older);
    }
    if (!this._pageInfo?.hasNextPage && this._remotePageInfo?.hasNextPage !== false) {
      const importButton = this._element("button", "older", this._loading ? "Loading…" : "Load earlier messages from Abler");
      importButton.type = "button";
      importButton.disabled = this._loading;
      importButton.title = "Fetch and save one page of up to 30 messages";
      importButton.addEventListener("click", () => this._importOlder());
      chat.append(importButton);
    }
    if (this._loading && !this._messages.length) chat.append(this._element("div", "status", "Loading messages…"));
    if (this._error) chat.append(this._element("div", "status", this._error));
    if (!this._loading && !this._error && !this._messages.length) chat.append(this._element("div", "status", "No stored messages in this conversation yet."));
    const viewerId = this._state()?.attributes?.viewer_id;
    let lastDay = "";
    for (const message of this._messages.slice().reverse()) {
      const day = this._date(message.createdAt, { day:"numeric", month:"short", year:"numeric" });
      if (day !== lastDay) { chat.append(this._element("div", "day", day)); lastDay = day; }
      const sender = message.creator?.displayName || "Unknown sender";
      const mine = viewerId && message.creator?.id === viewerId;
      const row = this._element("div", `message-row${mine ? " mine" : ""}`);
      row.append(this._element("div", "avatar", this._initials(sender)));
      const content = this._element("div", "message-content");
      content.append(this._element("div", "sender", sender));
      const bubble = this._element("div", "bubble", message.messageBody || "");
      for (const attachment of message.attachments || []) {
        bubble.append(this._element("span", "attachment", `📎 ${attachment.fileName || attachment.description || "Attachment"}`));
      }
      content.append(bubble);
      content.append(this._element("div", "time", this._date(message.createdAt, { hour:"2-digit", minute:"2-digit" })));
      row.append(content);
      chat.append(row);
    }
    body.append(chat);
    body.append(this._element("div", "footer", "Read-only in Home Assistant · New messages are checked hourly during the day"));
  }
}

customElements.define("sportabler-chat-card", SportablerChatCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "sportabler-chat-card",
  name: "Abler messages",
  description: "Read-only Abler conversation view",
  preview: false,
  documentationURL: "https://github.com/gunnaroi/ha-sportabler",
});
