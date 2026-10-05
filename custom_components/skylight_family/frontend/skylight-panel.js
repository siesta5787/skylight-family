/*
 * Skylight sidebar panel.
 *
 * Deliberately a plain custom element with no imports and no build step:
 * it ships as-is inside the Python integration, so HACS can install the
 * whole thing as one custom component. That rules out lit/HA's own frontend
 * components (not importable from a static file with any stability), hence
 * the hand-rolled innerHTML rendering and delegated click handling below.
 *
 * Home Assistant sets the `hass`, `narrow`, `route` and `panel` properties
 * on this element. `hass` is set again on every state change, so rendering
 * must NOT be driven by it — we render once on load and then only after an
 * action that changed something.
 */

const WEEKDAY_FALLBACK = [
  { key: "mon", label: "Monday" },
  { key: "tue", label: "Tuesday" },
  { key: "wed", label: "Wednesday" },
  { key: "thu", label: "Thursday" },
  { key: "fri", label: "Friday" },
  { key: "sat", label: "Saturday" },
  { key: "sun", label: "Sunday" },
];

const esc = (value) =>
  String(value == null ? "" : value).replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char],
  );

const rgbToHex = (rgb) => {
  if (!Array.isArray(rgb) || rgb.length < 3) return "#5b8def";
  return (
    "#" +
    rgb
      .slice(0, 3)
      .map((n) => Math.max(0, Math.min(255, Number(n) || 0)).toString(16).padStart(2, "0"))
      .join("")
  );
};

const hexToRgb = (hex) => {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!match) return null;
  const int = parseInt(match[1], 16);
  return [(int >> 16) & 255, (int >> 8) & 255, int & 255];
};

const STYLES = `
  :host {
    display: block;
    height: 100%;
    background: var(--primary-background-color, #f2f4f7);
    color: var(--primary-text-color, #212121);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
    --skylight-gap: 16px;
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    height: var(--header-height, 56px);
    padding: 0 16px;
    box-sizing: border-box;
    background: var(--app-header-background-color, var(--primary-color, #03a9f4));
    color: var(--app-header-text-color, #fff);
    font-size: 20px;
    font-weight: 400;
    position: sticky;
    top: 0;
    z-index: 2;
  }
  .toolbar .title { flex: 1; }
  .icon-button {
    background: none;
    border: 0;
    color: inherit;
    cursor: pointer;
    padding: 8px;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
  }
  .icon-button:hover { background: rgba(255, 255, 255, 0.12); }
  .icon-button svg { width: 24px; height: 24px; fill: currentColor; }
  .menu-button { display: none; }
  :host([data-narrow]) .menu-button { display: inline-flex; }

  .tabs {
    display: flex;
    gap: 4px;
    background: var(--app-header-background-color, var(--primary-color, #03a9f4));
    color: var(--app-header-text-color, #fff);
    padding: 0 8px;
    position: sticky;
    top: var(--header-height, 56px);
    z-index: 2;
  }
  .tab {
    background: none;
    border: 0;
    border-bottom: 3px solid transparent;
    color: inherit;
    opacity: 0.75;
    cursor: pointer;
    font: inherit;
    font-size: 14px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    padding: 14px 16px 11px;
  }
  .tab[aria-selected="true"] { opacity: 1; border-bottom-color: currentColor; }

  .content { padding: var(--skylight-gap); max-width: 1100px; margin: 0 auto; }
  .intro {
    color: var(--secondary-text-color, #727272);
    font-size: 14px;
    margin: 0 0 var(--skylight-gap);
  }
  .card {
    background: var(--card-background-color, #fff);
    border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0, 0, 0, 0.1));
    padding: 16px;
    margin-bottom: var(--skylight-gap);
  }
  .card-head {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .card-head h2 { font-size: 18px; font-weight: 500; margin: 0; flex: 1; }
  .dot {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    flex: none;
    box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.15);
  }
  .sub {
    color: var(--secondary-text-color, #727272);
    font-size: 13px;
    margin: 4px 0 0;
    word-break: break-word;
  }
  .section-label {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--secondary-text-color, #727272);
    margin: 18px 0 8px;
  }
  .routine {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 10px;
  }
  .day label {
    display: block;
    font-size: 12px;
    color: var(--secondary-text-color, #727272);
    margin-bottom: 4px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 14px;
  }
  .row .grow { flex: 1; min-width: 160px; }
  .field { margin-top: 12px; }
  .field > label {
    display: block;
    font-size: 12px;
    color: var(--secondary-text-color, #727272);
    margin-bottom: 4px;
  }
  select, input[type="text"], textarea {
    width: 100%;
    box-sizing: border-box;
    font: inherit;
    font-size: 14px;
    padding: 8px 10px;
    color: var(--primary-text-color, #212121);
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 6px;
  }
  textarea { min-height: 128px; resize: vertical; line-height: 1.5; }
  input[type="color"] {
    width: 48px;
    height: 36px;
    padding: 2px;
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 6px;
    background: none;
  }
  .checklist {
    max-height: 180px;
    overflow: auto;
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 6px;
    padding: 8px 10px;
  }
  .checklist label {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 14px;
    padding: 3px 0;
  }
  button.action {
    font: inherit;
    font-size: 14px;
    font-weight: 500;
    letter-spacing: 0.3px;
    padding: 8px 16px;
    border-radius: 6px;
    border: 1px solid var(--divider-color, #e0e0e0);
    background: var(--card-background-color, #fff);
    color: var(--primary-color, #03a9f4);
    cursor: pointer;
  }
  button.action:hover { background: rgba(0, 0, 0, 0.04); }
  button.action.primary {
    background: var(--primary-color, #03a9f4);
    border-color: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
  }
  button.action.danger { color: var(--error-color, #db4437); }
  button.action[disabled] { opacity: 0.5; cursor: default; }
  ul.items { margin: 8px 0 0; padding-left: 20px; font-size: 14px; }
  ul.items li { margin: 3px 0; }
  .empty {
    color: var(--secondary-text-color, #727272);
    font-size: 14px;
    font-style: italic;
  }
  .spacer { flex: 1; }
`;

const MENU_ICON =
  '<svg viewBox="0 0 24 24"><path d="M3 6h18v2H3V6m0 5h18v2H3v-2m0 5h18v2H3v-2Z"/></svg>';
const REFRESH_ICON =
  '<svg viewBox="0 0 24 24"><path d="M17.65 6.35A8 8 0 1 0 19.73 14h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35Z"/></svg>';

class SkylightFamilyPanel extends HTMLElement {
  constructor() {
    super();
    this._shadow = this.attachShadow({ mode: "open" });
    this._tab = "people";
    this._data = null;
    this._loadError = null;
    this._busy = false;
    // Which card currently has its inline form open, if any.
    this._editingMember = null;
    this._editingPreset = null;
    this._started = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._shadow.addEventListener("click", (ev) => this._onClick(ev));
      this._load();
    }
  }

  set narrow(value) {
    if (value) this.setAttribute("data-narrow", "");
    else this.removeAttribute("data-narrow");
  }

  set route(value) {
    this._route = value;
  }

  set panel(value) {
    this._panel = value;
  }

  /* ---------------------------------------------------------------- data */

  async _load() {
    try {
      this._data = await this._hass.callWS({ type: "skylight_family/config" });
      this._loadError = null;
    } catch (err) {
      this._loadError = err && (err.message || err.code) ? err.message || err.code : String(err);
    }
    this._render();
  }

  async _call(message, successText) {
    if (this._busy) return null;
    this._busy = true;
    this._render();
    try {
      const result = await this._hass.callWS(message);
      if (successText) this._notify(successText(result));
      return result;
    } catch (err) {
      this._notify(
        "Skylight: " + (err && (err.message || err.code) ? err.message || err.code : String(err)),
      );
      return null;
    } finally {
      this._busy = false;
      // Every mutation reloads the config entry server-side, so re-read
      // rather than trying to patch local state by hand.
      await this._load();
    }
  }

  _notify(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _entities(domain) {
    const states = (this._hass && this._hass.states) || {};
    return Object.keys(states)
      .filter((entityId) => entityId.startsWith(domain + "."))
      .map((entityId) => ({
        entityId,
        name:
          (states[entityId].attributes && states[entityId].attributes.friendly_name) || entityId,
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  _presets() {
    return (this._data && this._data.presets) || [];
  }

  _weekdays() {
    return (this._data && this._data.weekdays) || WEEKDAY_FALLBACK;
  }

  /* -------------------------------------------------------------- render */

  _render() {
    const body = this._loadError
      ? `<div class="content"><div class="card"><p class="empty">Couldn't load Skylight data: ${esc(
          this._loadError,
        )}</p><button class="action" data-action="reload">Try again</button></div></div>`
      : !this._data
        ? `<div class="content"><p class="empty">Loading…</p></div>`
        : !this._data.configured
          ? `<div class="content"><div class="card"><h2>Not set up yet</h2><p class="sub">Add the Skylight Family integration from Settings &rarr; Devices &amp; services first, then come back here.</p></div></div>`
          : this._tab === "people"
            ? this._renderPeople()
            : this._renderPresets();

    this._shadow.innerHTML = `
      <style>${STYLES}</style>
      <div class="toolbar">
        <button class="icon-button menu-button" data-action="menu" title="Sidebar">${MENU_ICON}</button>
        <div class="title">Skylight</div>
        <button class="icon-button" data-action="reload" title="Refresh">${REFRESH_ICON}</button>
      </div>
      <div class="tabs" role="tablist">
        <button class="tab" role="tab" data-action="tab" data-tab="people"
          aria-selected="${this._tab === "people"}">People</button>
        <button class="tab" role="tab" data-action="tab" data-tab="presets"
          aria-selected="${this._tab === "presets"}">Presets</button>
      </div>
      ${body}
    `;
  }

  _renderPeople() {
    const members = this._data.members || [];
    const presets = this._presets();
    const resetTime = String(this._data.reset_time || "").slice(0, 5);

    if (!members.length) {
      return `<div class="content"><div class="card"><h2>No family members yet</h2>
        <p class="sub">Add them from Settings &rarr; Devices &amp; services &rarr; Skylight Family
        &rarr; Add family member. Once they exist you can manage their routines here.</p></div></div>`;
    }

    return `<div class="content">
      <p class="intro">Each morning at ${esc(resetTime)} Skylight clears off completed
      to-dos and adds that day's preset for everyone below.</p>
      ${members.map((member) => this._renderMemberCard(member, presets)).join("")}
    </div>`;
  }

  _renderMemberCard(member, presets) {
    const id = member.subentry_id;
    const todo = member.todo_entity_id;
    const linked = [member.person_entity_id, todo].filter(Boolean).join(" · ");
    const calendarCount = (member.calendar_entity_ids || []).length;

    const dayFields = this._weekdays()
      .map((day) => {
        const selected = (member.routine || {})[day.key] || "";
        return `<div class="day">
          <label for="r-${esc(id)}-${esc(day.key)}">${esc(day.label)}</label>
          <select id="r-${esc(id)}-${esc(day.key)}" data-routine-day="${esc(day.key)}">
            ${this._presetOptions(presets, selected, "— none —")}
          </select>
        </div>`;
      })
      .join("");

    const applyRow = todo
      ? `<div class="row">
          <div class="grow">
            <select data-apply-select="${esc(id)}">
              ${this._presetOptions(presets, "", "Choose a preset…")}
            </select>
          </div>
          <button class="action" data-action="apply" data-member="${esc(id)}"
            ${this._busy ? "disabled" : ""}>Apply now</button>
        </div>`
      : `<p class="sub">No to-do list is linked, so presets can't be applied. Pick one under
         Linked entities below.</p>`;

    return `<div class="card" data-member-card="${esc(id)}">
      <div class="card-head">
        <span class="dot" style="background:${esc(rgbToHex(member.color))}"></span>
        <h2>${esc(member.name)}</h2>
        <button class="action" data-action="toggle-member" data-member="${esc(id)}">
          ${this._editingMember === id ? "Close" : "Linked entities"}</button>
      </div>
      <p class="sub">${esc(linked || "Nothing linked yet")}${
        calendarCount
          ? ` · ${calendarCount} calendar${calendarCount === 1 ? "" : "s"}`
          : " · no calendars"
      }</p>

      ${this._editingMember === id ? this._renderMemberForm(member) : ""}

      <div class="section-label">Weekly routine</div>
      <div class="routine" data-routine="${esc(id)}">${dayFields}</div>
      <div class="row">
        <button class="action primary" data-action="save-routine" data-member="${esc(id)}"
          ${this._busy ? "disabled" : ""}>Save routine</button>
        <span class="spacer"></span>
      </div>

      <div class="section-label">Push a preset right now</div>
      ${applyRow}
    </div>`;
  }

  _renderMemberForm(member) {
    const id = member.subentry_id;
    const people = this._entities("person");
    const todos = this._entities("todo");
    const calendars = this._entities("calendar");
    const selectedCalendars = new Set(member.calendar_entity_ids || []);

    return `<div data-member-form="${esc(id)}">
      <div class="field">
        <label for="m-${esc(id)}-name">Name</label>
        <input type="text" id="m-${esc(id)}-name" data-field="name" value="${esc(member.name)}">
      </div>
      <div class="field">
        <label for="m-${esc(id)}-person">Person</label>
        <select id="m-${esc(id)}-person" data-field="person">
          ${this._entityOptions(people, member.person_entity_id, "— none —")}
        </select>
      </div>
      <div class="field">
        <label for="m-${esc(id)}-todo">To-do list</label>
        <select id="m-${esc(id)}-todo" data-field="todo">
          ${this._entityOptions(todos, member.todo_entity_id, "— none —")}
        </select>
      </div>
      <div class="field">
        <label>Calendars</label>
        <div class="checklist" data-field="calendars">
          ${
            calendars.length
              ? calendars
                  .map(
                    (entity) => `<label><input type="checkbox" value="${esc(entity.entityId)}"
                      ${selectedCalendars.has(entity.entityId) ? "checked" : ""}>
                      ${esc(entity.name)}</label>`,
                  )
                  .join("")
              : '<span class="empty">No calendar entities found.</span>'
          }
        </div>
      </div>
      <div class="field">
        <label for="m-${esc(id)}-color">Colour</label>
        <input type="color" id="m-${esc(id)}-color" data-field="color"
          value="${esc(rgbToHex(member.color))}">
      </div>
      <div class="row">
        <button class="action primary" data-action="save-member" data-member="${esc(id)}"
          ${this._busy ? "disabled" : ""}>Save</button>
        <button class="action" data-action="toggle-member" data-member="${esc(id)}">Cancel</button>
      </div>
    </div>`;
  }

  _renderPresets() {
    const presets = this._presets();
    const members = this._data.members || [];

    return `<div class="content">
      <p class="intro">A preset is just a named list of to-do items. Assign them to days
      on the People tab, or push one onto someone's list straight away.</p>
      <div class="row" style="margin-top:0;margin-bottom:var(--skylight-gap)">
        <button class="action primary" data-action="new-preset"
          ${this._busy ? "disabled" : ""}>Add preset</button>
      </div>
      ${this._editingPreset === "new" ? this._renderPresetForm(null) : ""}
      ${
        presets.length
          ? presets.map((preset) => this._renderPresetCard(preset, members)).join("")
          : '<div class="card"><p class="empty">No presets yet.</p></div>'
      }
    </div>`;
  }

  _renderPresetCard(preset, members) {
    const id = preset.subentry_id;
    if (this._editingPreset === id) {
      return `<div class="card">${this._renderPresetForm(preset)}</div>`;
    }

    const usableMembers = members.filter((member) => member.todo_entity_id);

    return `<div class="card">
      <div class="card-head">
        <h2>${esc(preset.name)}</h2>
        <button class="action" data-action="edit-preset" data-preset="${esc(id)}">Edit</button>
        <button class="action danger" data-action="delete-preset" data-preset="${esc(id)}"
          ${this._busy ? "disabled" : ""}>Delete</button>
      </div>
      ${
        preset.items.length
          ? `<ul class="items">${preset.items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
          : '<p class="empty">No items — applying this preset does nothing.</p>'
      }
      ${
        usableMembers.length
          ? `<div class="row">
              <div class="grow">
                <select data-apply-member="${esc(id)}">
                  <option value="">Apply to…</option>
                  ${usableMembers
                    .map(
                      (member) =>
                        `<option value="${esc(member.subentry_id)}">${esc(member.name)}</option>`,
                    )
                    .join("")}
                </select>
              </div>
              <button class="action" data-action="apply-from-preset" data-preset="${esc(id)}"
                ${this._busy ? "disabled" : ""}>Apply now</button>
            </div>`
          : ""
      }
    </div>`;
  }

  _renderPresetForm(preset) {
    const id = preset ? preset.subentry_id : "new";
    return `<div data-preset-form="${esc(id)}">
      <div class="field">
        <label for="p-${esc(id)}-name">Preset name</label>
        <input type="text" id="p-${esc(id)}-name" data-field="name"
          placeholder="Morning routine" value="${esc(preset ? preset.name : "")}">
      </div>
      <div class="field">
        <label for="p-${esc(id)}-items">To-do items — one per line</label>
        <textarea id="p-${esc(id)}-items" data-field="items"
          placeholder="Brush teeth&#10;Make bed">${esc(
            preset ? preset.items.join("\n") : "",
          )}</textarea>
      </div>
      <div class="row">
        <button class="action primary" data-action="save-preset" data-preset="${esc(id)}"
          ${this._busy ? "disabled" : ""}>Save</button>
        <button class="action" data-action="cancel-preset">Cancel</button>
      </div>
    </div>`;
  }

  _presetOptions(presets, selected, placeholder) {
    return (
      `<option value=""${selected ? "" : " selected"}>${esc(placeholder)}</option>` +
      presets
        .map(
          (preset) =>
            `<option value="${esc(preset.subentry_id)}"${
              preset.subentry_id === selected ? " selected" : ""
            }>${esc(preset.name)}</option>`,
        )
        .join("")
    );
  }

  _entityOptions(entities, selected, placeholder) {
    return (
      `<option value=""${selected ? "" : " selected"}>${esc(placeholder)}</option>` +
      entities
        .map(
          (entity) =>
            `<option value="${esc(entity.entityId)}"${
              entity.entityId === selected ? " selected" : ""
            }>${esc(entity.name)}</option>`,
        )
        .join("")
    );
  }

  /* ------------------------------------------------------------- actions */

  _onClick(ev) {
    const target = ev.composedPath().find((node) => node.dataset && node.dataset.action);
    if (!target) return;
    const { action } = target.dataset;

    if (action === "menu") {
      this.dispatchEvent(
        new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }),
      );
      return;
    }
    if (action === "tab") {
      this._tab = target.dataset.tab;
      this._editingMember = null;
      this._editingPreset = null;
      this._render();
      return;
    }
    if (action === "reload") {
      this._load();
      return;
    }
    if (action === "toggle-member") {
      this._editingMember = this._editingMember === target.dataset.member ? null : target.dataset.member;
      this._render();
      return;
    }
    if (action === "save-routine") {
      this._saveRoutine(target.dataset.member);
      return;
    }
    if (action === "save-member") {
      this._saveMember(target.dataset.member);
      return;
    }
    if (action === "apply") {
      const select = this._shadow.querySelector(`[data-apply-select="${target.dataset.member}"]`);
      this._applyPreset(target.dataset.member, select && select.value);
      return;
    }
    if (action === "apply-from-preset") {
      const select = this._shadow.querySelector(`[data-apply-member="${target.dataset.preset}"]`);
      this._applyPreset(select && select.value, target.dataset.preset);
      return;
    }
    if (action === "new-preset") {
      this._editingPreset = "new";
      this._render();
      return;
    }
    if (action === "edit-preset") {
      this._editingPreset = target.dataset.preset;
      this._render();
      return;
    }
    if (action === "cancel-preset") {
      this._editingPreset = null;
      this._render();
      return;
    }
    if (action === "save-preset") {
      this._savePreset(target.dataset.preset);
      return;
    }
    if (action === "delete-preset") {
      this._deletePreset(target.dataset.preset);
    }
  }

  _saveRoutine(memberId) {
    const container = this._shadow.querySelector(`[data-routine="${memberId}"]`);
    if (!container) return;
    const routine = {};
    container.querySelectorAll("[data-routine-day]").forEach((select) => {
      routine[select.dataset.routineDay] = select.value || null;
    });
    this._call(
      {
        type: "skylight_family/member/update",
        subentry_id: memberId,
        routine,
      },
      (result) => `Saved ${result.name}'s weekly routine`,
    );
  }

  _saveMember(memberId) {
    const form = this._shadow.querySelector(`[data-member-form="${memberId}"]`);
    if (!form) return;
    const value = (field) => {
      const node = form.querySelector(`[data-field="${field}"]`);
      return node ? node.value : null;
    };
    const name = (value("name") || "").trim();
    if (!name) {
      this._notify("Skylight: a family member needs a name");
      return;
    }
    const calendars = Array.from(
      form.querySelectorAll('[data-field="calendars"] input:checked'),
    ).map((input) => input.value);

    this._editingMember = null;
    this._call(
      {
        type: "skylight_family/member/update",
        subentry_id: memberId,
        name,
        person_entity_id: value("person") || "",
        todo_entity_id: value("todo") || null,
        calendar_entity_ids: calendars,
        color: hexToRgb(value("color")),
      },
      (result) => `Saved ${result.name}`,
    );
  }

  _savePreset(presetId) {
    const form = this._shadow.querySelector(`[data-preset-form="${presetId}"]`);
    if (!form) return;
    const name = form.querySelector('[data-field="name"]').value.trim();
    if (!name) {
      this._notify("Skylight: a preset needs a name");
      return;
    }
    const items = form
      .querySelector('[data-field="items"]')
      .value.split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

    this._editingPreset = null;
    this._call(
      presetId === "new"
        ? { type: "skylight_family/preset/create", name, items }
        : { type: "skylight_family/preset/update", subentry_id: presetId, name, items },
      (result) => `Saved preset "${result.name}"`,
    );
  }

  _deletePreset(presetId) {
    const preset = this._presets().find((candidate) => candidate.subentry_id === presetId);
    const name = preset ? preset.name : "this preset";
    if (!confirm(`Delete the "${name}" preset? Any day it's assigned to will be cleared.`)) {
      return;
    }
    this._call({ type: "skylight_family/preset/delete", subentry_id: presetId }, () => `Deleted "${name}"`);
  }

  _applyPreset(memberId, presetId) {
    if (!memberId) {
      this._notify("Skylight: pick a family member first");
      return;
    }
    if (!presetId) {
      this._notify("Skylight: pick a preset first");
      return;
    }
    this._call(
      { type: "skylight_family/apply_preset", member_id: memberId, preset_id: presetId },
      (result) =>
        result.added
          ? `Added ${result.added} item${result.added === 1 ? "" : "s"} to ${result.member}'s list`
          : `${result.member}'s list already had everything in "${result.preset}"`,
    );
  }
}

customElements.define("skylight-family-panel", SkylightFamilyPanel);
