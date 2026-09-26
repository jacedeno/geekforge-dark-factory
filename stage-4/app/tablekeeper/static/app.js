/* Tablekeeper browser client. Plain JavaScript, no external dependencies.
 *
 * Every piece of data from the server is inserted as text, never as HTML. The server stays
 * authoritative: a confirmation is only ever rendered from a successful booking response. */
(() => {
  "use strict";

  const SESSION_KEY = "tablekeeper.session";
  const REQUEST_TIMEOUT_MS = 12000;

  // ---------------------------------------------------------------- session (E2)

  function currentSession() {
    try {
      const s = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
      return s && typeof s.token === "string" ? s : null;
    } catch (e) {
      return null;
    }
  }

  function saveSession(data) {
    localStorage.setItem(SESSION_KEY, JSON.stringify({
      token: data.token, user_id: data.user_id, display_name: data.display_name,
    }));
  }

  function forgetSession() {
    localStorage.removeItem(SESSION_KEY);
  }

  // ---------------------------------------------------------------- DOM helpers

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "testid") el.setAttribute("data-testid", v);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  function newKey() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return "tk-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function todayIso() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  // ---------------------------------------------------------------- formatting (E8)

  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
    "Dec"];

  function formatDate(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    return `${WEEKDAYS[weekday]} ${d} ${MONTHS[m - 1]} ${y}`;
  }

  function formatLocal(local) {
    return `${formatDate(local.slice(0, 10))} · ${local.slice(11, 16)}`;
  }

  function tableLabel(restaurant, id) {
    const table = restaurant && (restaurant.tables || []).find((t) => t.id === id);
    const label = table && typeof table.label === "string" && table.label !== "" ? table.label : id;
    return /^\d+$/.test(label) ? `Table ${label}` : label;
  }

  function tablesLabel(restaurant, ids) {
    return ids.map((id) => tableLabel(restaurant, id)).join(" + ");
  }

  function seats(restaurant, ids) {
    return ids.reduce((sum, id) => {
      const t = (restaurant.tables || []).find((x) => x.id === id);
      return sum + (t ? t.capacity : 0);
    }, 0);
  }

  function reservationTables(r) {
    return Array.isArray(r.table_ids) ? r.table_ids : [r.table_id];
  }

  function partyText(n) {
    return n === 1 ? "1 guest" : `${n} guests`;
  }

  // ---------------------------------------------------------------- API

  async function api(method, path, { body, key, auth } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth) {
      const s = currentSession();
      if (s) headers.Authorization = `Bearer ${s.token}`;
    }
    if (key) headers["Idempotency-Key"] = key;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      let response;
      try {
        response = await fetch(path, {
          method, headers, cache: "no-store", signal: controller.signal,
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      } catch (e) {
        return { kind: "network" };
      }
      let data = null;
      try {
        const text = await response.text();
        data = text ? JSON.parse(text) : null;
      } catch (e) {
        return { kind: "unreadable", status: response.status };
      }
      return { kind: "response", status: response.status, data };
    } finally {
      clearTimeout(timer);
    }
  }

  function errorCode(result) {
    return result.data && result.data.error ? result.data.error.code : null;
  }

  function serverMessage(result) {
    const m = result.data && result.data.error && result.data.error.message;
    return typeof m === "string" && m ? m.charAt(0).toUpperCase() + m.slice(1) + "." : null;
  }

  const BOOKING_MESSAGES = {
    table_unavailable: "Someone has just taken this table for that time. Availability has been " +
      "refreshed — pick another table or time, or keep this form and try again.",
    party_exceeds_capacity: "That party is larger than this seating allows. Choose a bigger table " +
      "or a combined option.",
    combination_not_allowed: "These tables can't be combined at this restaurant.",
    outside_opening_hours: "The restaurant isn't open for the whole booking at that time.",
    not_on_slot_grid: "Please choose one of the listed start times.",
    invalid_local_time: "That time doesn't exist on this date because the clocks change.",
    unauthenticated: "Your session has ended. Please log in again to book.",
    not_found: "That restaurant or table is no longer available.",
    idempotency_key_reuse: "This booking attempt clashes with an earlier one. Please pick the table " +
      "again.",
  };

  // ---------------------------------------------------------------- header and routing

  const header = document.getElementById("site-header");
  const main = document.getElementById("main");

  function link(path, text, cls) {
    return h("a", {
      href: path, class: cls, "aria-current": location.pathname === path ? "page" : null,
      onclick: (ev) => {
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
        ev.preventDefault();
        navigate(path);
      },
    }, text);
  }

  function renderHeader() {
    const s = currentSession();
    const brand = h("a", {
      class: "brand", href: "/",
      onclick: (ev) => { ev.preventDefault(); navigate("/"); },
    });
    brand.innerHTML = '<svg viewBox="0 0 32 32" aria-hidden="true" class="brand-mark">' +
      '<rect width="32" height="32" rx="8"/><path d="M9 11h14M11 11v11M21 11v11M9 16h14"/></svg>';
    brand.append(h("span", { class: "brand-name" }, "Tablekeeper"));

    const nav = h("nav", { class: "main-nav", "aria-label": "Main" },
      link("/", "Find a table", "nav-link"),
      link("/lookup", "My reservation", "nav-link"));

    let account;
    if (s) {
      const name = s.display_name || "Guest";
      account = h("div", { class: "account" },
        h("span", { class: "current-user", testid: "current-user" },
          h("span", { class: "avatar", "aria-hidden": "true" }, name.trim().charAt(0).toUpperCase() || "·"),
          h("span", { class: "current-user-name" }, name)),
        h("button", {
          type: "button", class: "btn btn-quiet", testid: "logout-button",
          onclick: () => { forgetSession(); closeBooking(); render(); },
        }, "Log out"));
    } else {
      account = h("div", { class: "account" },
        link("/login", "Log in", "nav-link"),
        link("/signup", "Sign up", "btn btn-small btn-primary"));
    }
    header.replaceChildren(h("div", { class: "header-inner" }, brand, nav, account));
  }

  const SCREENS = { "/": renderSearch, "/signup": renderSignup, "/login": renderLogin,
    "/lookup": renderLookup };

  function navigate(path) {
    if (path !== location.pathname) history.pushState({}, "", path);
    render();
    window.scrollTo(0, 0);
  }

  function render() {
    renderHeader();
    const screen = SCREENS[location.pathname] || renderSearch;
    main.replaceChildren();
    screen();
  }

  window.addEventListener("popstate", render);

  // ---------------------------------------------------------------- signup and login

  function field(label, input, hint) {
    const id = input.id;
    return h("div", { class: "field" },
      h("label", { for: id, class: "field-label" }, label),
      input,
      hint ? h("p", { class: "field-hint", id: `${id}-hint` }, hint) : null);
  }

  function authScreen({ title, lead, fields, submitText, busyText, testid, send, alt }) {
    const errorSlot = h("div", { class: "slot" });
    const submit = h("button", { type: "submit", class: "btn btn-primary btn-block", testid },
      submitText);
    const form = h("form", {
      class: "card auth-card", novalidate: true,
      onsubmit: async (ev) => {
        ev.preventDefault();
        errorSlot.replaceChildren();
        submit.setAttribute("aria-busy", "true");
        submit.classList.add("is-loading");
        submit.textContent = busyText;
        const result = await send();
        submit.removeAttribute("aria-busy");
        submit.classList.remove("is-loading");
        submit.textContent = submitText;
        if (result.kind === "response" && result.status >= 200 && result.status < 300 &&
            result.data && typeof result.data.token === "string") {
          saveSession(result.data);
          navigate("/");
          return;
        }
        errorSlot.replaceChildren(h("p", { class: "notice notice-refused", role: "alert",
          testid: "auth-error" }, authMessage(result)));
      },
    },
    h("h1", { class: "screen-title" }, title),
    h("p", { class: "lead" }, lead),
    fields, errorSlot, submit, alt);
    main.append(h("section", { class: "auth-layout" }, form,
      h("aside", { class: "auth-aside", "aria-hidden": "true" },
        h("p", { class: "aside-quote" }, "“A good evening starts with the right table.”"),
        h("p", { class: "aside-note" }, "Book in seconds, look up or cancel any time."))));
  }

  function authMessage(result) {
    if (result.kind !== "response") {
      return "We couldn't reach Tablekeeper. Check your connection and try again.";
    }
    const code = errorCode(result);
    if (code === "email_taken") return "An account with this email already exists. Try logging in.";
    if (code === "unauthenticated") return "That email and password don't match an account.";
    return serverMessage(result) || "Something went wrong. Please try again.";
  }

  function renderSignup() {
    const email = h("input", { id: "signup-email", type: "email", autocomplete: "email",
      testid: "signup-email", required: true });
    const password = h("input", { id: "signup-password", type: "password", minlength: 8,
      autocomplete: "new-password", testid: "signup-password", required: true,
      "aria-describedby": "signup-password-hint" });
    const name = h("input", { id: "signup-display-name", type: "text", autocomplete: "name",
      testid: "signup-display-name", required: true });
    authScreen({
      title: "Create your account",
      lead: "Save your details once and book any table in a couple of taps.",
      fields: [field("Your name", name), field("Email", email),
        field("Password", password, "At least 8 characters.")],
      submitText: "Create account", busyText: "Creating account…", testid: "signup-submit",
      send: () => api("POST", "/auth/signup", { body: {
        email: email.value, password: password.value, display_name: name.value } }),
      alt: h("p", { class: "auth-alt" }, "Already have an account? ", link("/login", "Log in")),
    });
  }

  function renderLogin() {
    const email = h("input", { id: "login-email", type: "email", autocomplete: "email",
      testid: "login-email", required: true });
    const password = h("input", { id: "login-password", type: "password",
      autocomplete: "current-password", testid: "login-password", required: true });
    authScreen({
      title: "Welcome back",
      lead: "Log in to book a table or manage your reservations.",
      fields: [field("Email", email), field("Password", password)],
      submitText: "Log in", busyText: "Logging in…", testid: "login-submit",
      send: () => api("POST", "/auth/login", { body: {
        email: email.value, password: password.value } }),
      alt: h("p", { class: "auth-alt" }, "New here? ", link("/signup", "Create an account")),
    });
  }

  // ---------------------------------------------------------------- search, grid and booking

  const search = {
    form: { restaurantId: "", date: todayIso(), party: "2" },
    restaurants: null, // list from GET /restaurants, null until loaded
    restaurantsError: false,
    seq: 0, // number of the latest search started (E5)
    current: null, // parameters of the latest search started
    view: { status: "idle" },
    refreshing: false,
    authPrompt: false,
    selection: null, // the open booking form: {restaurant, tableIds, startsLocal, party}
    attempt: null, // booking attempt identity (E6): {key, body}
    outcome: null, // {kind: success|refused|uncertain, message, reservation}
    submitting: false,
  };
  let els = {}; // live elements of the search screen

  function renderSearch() {
    const select = h("select", { id: "restaurant-select", testid: "restaurant-select",
      onchange: () => { search.form.restaurantId = select.value; } });
    const date = h("input", { id: "date-input", type: "date", testid: "date-input",
      value: search.form.date, oninput: () => { search.form.date = date.value; } });
    const party = h("input", { id: "party-size-input", type: "number", min: 1, step: 1,
      inputmode: "numeric", testid: "party-size-input", value: search.form.party,
      oninput: () => { search.form.party = party.value; } });
    const searchError = h("div", { class: "slot" });
    const button = h("button", { type: "submit", class: "btn btn-primary search-button",
      testid: "search-button" }, "Show tables");
    const form = h("form", {
      class: "card search-card", novalidate: true,
      onsubmit: (ev) => { ev.preventDefault(); startSearch(); },
    },
    h("div", { class: "search-fields" },
      field("Restaurant", select), field("Date", date), field("Guests", party),
      h("div", { class: "field field-action" }, button)),
    searchError);

    const results = h("section", { class: "results", "aria-live": "polite",
      "aria-label": "Availability" });
    const booking = h("aside", { class: "booking-panel", "aria-label": "Your booking" });
    els = { select, date, party, searchError, results, booking };

    main.append(
      h("section", { class: "hero" },
        h("p", { class: "eyebrow" }, "Reserve a table"),
        h("h1", { class: "screen-title" }, "Find your table"),
        h("p", { class: "lead" }, "Pick a restaurant, a date and your party size to see every open table — " +
          "including tables that can be joined for bigger groups.")),
      form,
      h("div", { class: "search-layout" }, results, booking));

    fillRestaurants();
    loadRestaurants();
    renderResults();
    renderBooking();
  }

  async function loadRestaurants() {
    const result = await api("GET", "/restaurants");
    if (result.kind === "response" && result.status === 200 && result.data &&
        Array.isArray(result.data.restaurants)) {
      search.restaurants = result.data.restaurants;
      search.restaurantsError = false;
    } else {
      search.restaurantsError = true;
    }
    fillRestaurants();
  }

  function fillRestaurants() {
    const select = els.select;
    if (!select || !select.isConnected) return;
    const list = search.restaurants;
    if (!list) {
      if (search.restaurantsError) {
        select.replaceChildren(h("option", { value: "" }, "Restaurants unavailable"));
      } else if (!select.options.length) {
        select.replaceChildren(h("option", { value: "" }, "Loading restaurants…"));
      }
      return;
    }
    const chosen = select.value && list.some((r) => r.id === select.value) ? select.value :
      (list.some((r) => r.id === search.form.restaurantId) ? search.form.restaurantId :
        (list[0] ? list[0].id : ""));
    select.replaceChildren(...(list.length ? list.map((r) => h("option", { value: r.id }, r.name || r.id)) :
      [h("option", { value: "" }, "No restaurants yet")]));
    select.value = chosen;
    search.form.restaurantId = chosen;
  }

  function showSearchError(message) {
    els.searchError.replaceChildren(message ? h("p", { class: "notice notice-refused", role: "alert",
      testid: "search-error" }, message) : "");
  }

  function startSearch() {
    const restaurantId = els.select.value;
    const date = els.date.value;
    const partyValue = els.party.value.trim();
    if (!restaurantId) return showSearchError("Choose a restaurant first.");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return showSearchError("Choose a date.");
    if (!/^\d+$/.test(partyValue) || Number(partyValue) < 1) {
      return showSearchError("Enter the number of guests (1 or more).");
    }
    showSearchError(null);
    closeBooking(); // a new search closes a form that belongs to an older search (E5)
    search.authPrompt = false;
    runSearch({ restaurantId, date, party: Number(partyValue) }, false);
  }

  async function runSearch(params, refresh) {
    const seq = ++search.seq;
    search.current = params;
    if (refresh) {
      search.refreshing = true;
    } else {
      search.view = { status: "loading", params };
    }
    renderResults();
    const qs = new URLSearchParams({ restaurant_id: params.restaurantId, date: params.date,
      party_size: String(params.party) });
    const rid = encodeURIComponent(params.restaurantId);
    const [detail, availability, policies] = await Promise.all([
      api("GET", `/restaurants/${rid}`),
      api("GET", `/availability?${qs}`),
      api("GET", `/restaurants/${rid}/policies`),
    ]);
    if (seq !== search.seq) return; // a later search has started: never apply an older response
    search.refreshing = false;
    if (detail.kind === "response" && detail.status === 200 && availability.kind === "response" &&
        availability.status === 200 && availability.data && Array.isArray(availability.data.slots)) {
      const published = policies.kind === "response" && policies.status === 200 && policies.data &&
        Array.isArray(policies.data.policies) ? policies.data.policies : [];
      search.view = { status: "ready", params,
        restaurant: withPolicyCapacities(detail.data, published, params.date),
        slots: availability.data.slots };
    } else {
      const notFound = detail.status === 404 || availability.status === 404;
      search.view = { status: "error", params, message: notFound ?
        "That restaurant is no longer listed. Choose another one." :
        "We couldn't load availability just now. Please try again." };
    }
    renderResults();
  }

  // The policy for a date: greatest effective_from not later than it, ties by version; without
  // one, the restaurant's own rules apply. Table capacities shown and used for joined options
  // follow that policy.
  function withPolicyCapacities(restaurant, policies, date) {
    let best = null;
    for (const p of policies) {
      if (typeof p.effective_from !== "string" || p.effective_from > date) continue;
      if (!best || p.effective_from > best.effective_from ||
          (p.effective_from === best.effective_from && p.policy_version > best.policy_version)) {
        best = p;
      }
    }
    if (!best || !best.capacities) return restaurant;
    return Object.assign({}, restaurant, { tables: (restaurant.tables || []).map((t) =>
      Object.assign({}, t, { capacity: typeof best.capacities[t.id] === "number" ?
        best.capacities[t.id] : t.capacity })) });
  }

  function refreshSearch() {
    if (search.current) runSearch(search.current, true);
  }

  function pairsFor(restaurant, party) {
    const seen = new Set();
    const pairs = [];
    for (const p of restaurant.combinable || []) {
      const k = [...p].sort().join("\u0000");
      if (seen.has(k)) continue;
      seen.add(k);
      if (seats(restaurant, p) >= party) pairs.push(p);
    }
    return pairs;
  }

  function sameSet(a, b) {
    return a.length === b.length && a.every((x) => b.includes(x));
  }

  function isSelected(restaurant, ids, startsLocal) {
    const s = search.selection;
    return !!s && s.restaurant.id === restaurant.id && s.startsLocal === startsLocal &&
      sameSet(s.tableIds, ids);
  }

  function renderResults() {
    const box = els.results;
    if (!box || !box.isConnected) return;
    const view = search.view;
    const parts = [];
    if (search.authPrompt) {
      parts.push(h("div", { class: "notice notice-refused auth-prompt", role: "alert",
        testid: "auth-error" },
      h("strong", {}, "Log in to book this table. "),
      "Your search stays here. ", link("/login", "Log in"), " or ", link("/signup", "create an account"), "."));
    }
    if (view.status === "idle") {
      parts.push(h("div", { class: "empty-state" },
        h("p", { class: "empty-title" }, "Your evening starts here"),
        h("p", {}, "Choose a restaurant, date and number of guests, then select “Show tables”.")));
    } else if (view.status === "loading") {
      parts.push(h("div", { class: "loading-state", "aria-busy": "true" },
        h("p", { class: "loading-text" }, h("span", { class: "spinner", "aria-hidden": "true" }),
          "Checking open tables…"),
        [0, 1, 2].map(() => h("div", { class: "skeleton-row", "aria-hidden": "true" },
          h("span", { class: "skeleton skeleton-time" }),
          h("span", { class: "skeleton skeleton-chip" }), h("span", { class: "skeleton skeleton-chip" })))));
    } else if (view.status === "error") {
      parts.push(h("div", { class: "notice notice-refused", role: "alert" }, view.message, " ",
        h("button", { type: "button", class: "btn btn-small btn-quiet",
          onclick: () => runSearch(view.params, false) }, "Try again")));
    } else {
      parts.push(resultsHeader(view));
      if (!view.slots.length) {
        parts.push(h("div", { class: "empty-state", testid: "no-slots" },
          h("p", { class: "empty-title" }, "No tables on this day"),
          h("p", {}, `${view.restaurant.name || "The restaurant"} has no bookable times on ` +
            `${formatDate(view.params.date)}. Try another date.`)));
      } else {
        parts.push(grid(view));
      }
    }
    box.replaceChildren(...parts);
  }

  function resultsHeader(view) {
    return h("div", { class: "results-header" },
      h("div", {},
        h("h2", { class: "results-title" }, view.restaurant.name || view.restaurant.id),
        h("p", { class: "results-meta" }, `${formatDate(view.params.date)} · ${partyText(view.params.party)}`,
          search.refreshing ? h("span", { class: "refreshing" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Updating") : null)),
      h("ul", { class: "legend", "aria-label": "Legend" },
        h("li", {}, h("span", { class: "swatch swatch-open" }), "Open"),
        h("li", {}, h("span", { class: "swatch swatch-taken" }), "Unavailable"),
        h("li", {}, h("span", { class: "swatch swatch-selected" }), "Selected")));
  }

  function grid(view) {
    const { restaurant, slots, params } = view;
    const tables = restaurant.tables || [];
    const pairs = pairsFor(restaurant, params.party);
    const rows = slots.map((slot) => {
      const time = slot.starts_at_local.slice(11, 16);
      const openSingles = slot.available_table_ids || [];
      const openOptions = slot.available_options || [];
      const cells = tables.map((t) => cell(restaurant, slot, [t.id], time,
        openSingles.includes(t.id), t.capacity < params.party));
      for (const p of pairs) {
        cells.push(cell(restaurant, slot, p, time,
          openOptions.some((o) => Array.isArray(o.table_ids) && sameSet(o.table_ids, p)), false));
      }
      return h("div", { class: "slot-row", role: "group", "aria-label": `Tables at ${time}` },
        h("div", { class: "slot-time" }, h("span", { class: "slot-hour" }, time)),
        h("div", { class: "slot-cells" }, cells));
    });
    return h("div", { class: "availability-grid", testid: "availability-grid" }, rows);
  }

  function cell(restaurant, slot, ids, time, available, tooSmall) {
    const combined = ids.length > 1;
    const selected = available && isSelected(restaurant, ids, slot.starts_at_local);
    const label = tablesLabel(restaurant, ids);
    const capacity = seats(restaurant, ids);
    const state = !available ? (tooSmall ? "Too small" : "Taken") : (selected ? "Selected" : "Open");
    const cls = ["cell", available ? "is-open" : "is-taken", selected ? "is-selected" : "",
      combined ? "is-combined" : ""].join(" ");
    return h("button", {
      type: "button", class: cls, testid: `slot-${ids.join("+")}-${time}`,
      "data-available": available ? "true" : "false",
      "aria-pressed": available ? (selected ? "true" : "false") : null,
      "aria-label": `${label}, ${capacity} seats, ${time}, ${state.toLowerCase()}`,
      onclick: () => {
        if (!available) return; // clicking an unavailable cell does nothing
        chooseCell(restaurant, ids, slot.starts_at_local);
      },
    },
    h("span", { class: "cell-label" }, label),
    h("span", { class: "cell-meta" }, combined ? `Joined · ${capacity} seats` : `${capacity} seats`),
    h("span", { class: "cell-state" }, state));
  }

  function chooseCell(restaurant, ids, startsLocal) {
    if (!currentSession()) {
      search.authPrompt = true;
      renderResults();
      const prompt = els.results.querySelector("[data-testid=auth-error]");
      if (prompt) prompt.scrollIntoView({ block: "nearest" });
      return;
    }
    search.authPrompt = false;
    search.selection = { restaurant, tableIds: [...ids], startsLocal,
      party: search.current ? search.current.party : 1 };
    search.attempt = { key: newKey(), body: null }; // created when the form opens (E6)
    search.outcome = null;
    search.submitting = false;
    renderResults();
    renderBooking();
    if (els.booking.scrollIntoView) els.booking.scrollIntoView({ block: "nearest" });
  }

  function closeBooking() {
    search.selection = null;
    search.attempt = null;
    search.outcome = null;
    search.submitting = false;
    if (els.booking && els.booking.isConnected) renderBooking();
  }

  function renderBooking() {
    const panel = els.booking;
    if (!panel || !panel.isConnected) return;
    const sel = search.selection;
    if (!sel) {
      panel.replaceChildren(h("div", { class: "card booking-hint" },
        h("p", { class: "empty-title" }, "Choose a table"),
        h("p", {}, "Select any open table or joined option in the list to start your booking.")));
      els.bookingParts = null;
      return;
    }
    const restaurant = sel.restaurant;
    const partyInput = h("input", { id: "booking-party-size", type: "number", min: 1, step: 1,
      inputmode: "numeric", testid: "booking-party-size", value: String(sel.party) });
    const submit = h("button", { type: "submit", class: "btn btn-primary btn-block",
      testid: "booking-submit" }, "Confirm booking");
    const messages = h("div", { class: "slot" });
    const confirmation = h("div", { class: "slot" });
    const form = h("form", {
      class: "card booking-form", testid: "booking-form", novalidate: true,
      onsubmit: (ev) => { ev.preventDefault(); submitBooking(); },
    },
    h("div", { class: "booking-head" },
      h("p", { class: "eyebrow" }, "Your booking"),
      h("button", { type: "button", class: "btn btn-small btn-quiet", "aria-label": "Close booking",
        onclick: () => { closeBooking(); renderResults(); } }, "Close")),
    h("p", { class: "booking-summary", testid: "booking-summary" },
      h("span", { class: "summary-tables" }, tablesLabel(restaurant, sel.tableIds)),
      h("span", { class: "summary-when" }, formatLocal(sel.startsLocal)),
      h("span", { class: "summary-where" },
        `${restaurant.name || restaurant.id} · ${seats(restaurant, sel.tableIds)} seats`)),
    field("Guests", partyInput),
    submit, messages, confirmation);
    els.bookingParts = { partyInput, submit, messages, confirmation };
    panel.replaceChildren(form);
    renderOutcome();
  }

  function renderOutcome() {
    const parts = els.bookingParts;
    if (!parts) return;
    const { submit, messages, confirmation } = parts;
    submit.classList.toggle("is-loading", search.submitting);
    if (search.submitting) submit.setAttribute("aria-busy", "true");
    else submit.removeAttribute("aria-busy");
    submit.textContent = search.submitting ? "Booking…" : "Confirm booking";
    const outcome = search.outcome;
    messages.replaceChildren();
    confirmation.replaceChildren();
    if (!outcome) return;
    if (outcome.kind === "refused") {
      messages.append(h("p", { class: "notice notice-refused", role: "alert", testid: "booking-error" },
        outcome.message));
    } else if (outcome.kind === "uncertain") {
      messages.append(h("p", { class: "notice notice-uncertain", role: "status",
        testid: "booking-uncertain" }, outcome.message));
    } else if (outcome.kind === "success") {
      confirmation.append(confirmationCard(outcome.reservation, search.selection.restaurant));
    }
  }

  function confirmationCard(r, restaurant) {
    const tables = tablesLabel(restaurant, reservationTables(r));
    return h("div", { class: "confirmation", testid: "confirmation", role: "status" },
      h("p", { class: "confirmation-title" }, "Table confirmed"),
      h("p", { class: "confirmation-ref-line" }, "Reference ",
        h("strong", { class: "confirmation-reference", testid: "confirmation-reference" }, r.reference)),
      h("p", { class: "confirmation-details", testid: "confirmation-details" },
        `${restaurant.name || r.restaurant_id} · `,
        h("span", { testid: "confirmation-tables" }, tables),
        ` · ${formatLocal(r.starts_at_local)} · ${partyText(r.party_size)}`),
      h("p", { class: "confirmation-note" }, "Keep this reference to look up or cancel your booking."));
  }

  // A replayed confirmation is the original response; the booking's tables may have changed
  // since (for example after a seating change), so show the reservation as it is now.
  async function showCurrentTables(sel, outcome) {
    const ref = outcome.reservation.reference;
    const current = await api("GET", `/reservations/${encodeURIComponent(ref)}`, { auth: true });
    if (search.selection !== sel || search.outcome !== outcome) return;
    if (current.kind === "response" && current.status === 200 && current.data &&
        current.data.reference === ref) {
      outcome.reservation = current.data;
      renderOutcome();
    }
  }

  function bookingBody(sel, party) {
    const body = { restaurant_id: sel.restaurant.id };
    if (sel.tableIds.length === 1) body.table_id = sel.tableIds[0];
    else body.table_ids = [...sel.tableIds];
    body.starts_at_local = sel.startsLocal;
    body.party_size = party;
    return body;
  }

  async function submitBooking() {
    const sel = search.selection;
    const parts = els.bookingParts;
    if (!sel || !parts || search.submitting) return;
    const partyValue = parts.partyInput.value.trim();
    if (!/^\d+$/.test(partyValue) || Number(partyValue) < 1) {
      search.outcome = { kind: "refused", message: "Enter the number of guests (1 or more)." };
      renderOutcome();
      return;
    }
    const body = bookingBody(sel, Number(partyValue));
    const canonical = JSON.stringify(body);
    // Same body keeps the attempt's key (retries and resubmissions replay); any change is a new
    // booking request with a new key (E6).
    if (search.attempt.body !== null && search.attempt.body !== canonical) {
      search.attempt = { key: newKey(), body: canonical };
    } else {
      search.attempt.body = canonical;
    }
    const attempt = search.attempt;
    search.submitting = true;
    renderOutcome();
    const result = await api("POST", "/reservations", { body, key: attempt.key, auth: true });
    if (search.selection !== sel) return; // the form was closed or replaced meanwhile
    search.submitting = false;
    const ok = result.kind === "response" && (result.status === 200 || result.status === 201) &&
      result.data && typeof result.data.reference === "string";
    if (ok) {
      const outcome = { kind: "success", reservation: result.data };
      search.outcome = outcome;
      renderOutcome();
      refreshSearch();
      showCurrentTables(sel, outcome);
    } else if (result.kind === "network" || (result.status >= 500) ||
        (result.status >= 200 && result.status < 300)) {
      search.outcome = { kind: "uncertain", message: "We couldn't confirm whether this booking " +
        "went through. Your details are kept: select “Confirm booking” again to check — you " +
        "won't be booked twice." };
      renderOutcome();
    } else {
      const code = result.kind === "response" ? errorCode(result) : null;
      search.outcome = { kind: "refused", message: BOOKING_MESSAGES[code] ||
        (result.kind === "response" && serverMessage(result)) ||
        "The restaurant couldn't accept this booking. Please check the details." };
      renderOutcome();
      if (code === "table_unavailable") refreshSearch(); // keep the form and its inputs
    }
  }

  // ---------------------------------------------------------------- lookup (E9)

  let lookupSeq = 0;

  function renderLookup() {
    const input = h("input", { id: "lookup-reference-input", type: "text", autocomplete: "off",
      autocapitalize: "characters", spellcheck: "false", testid: "lookup-reference-input",
      placeholder: "e.g. K3P7QW8M" });
    const result = h("div", { class: "lookup-result", "aria-live": "polite" });
    const form = h("form", {
      class: "card lookup-card", novalidate: true,
      onsubmit: (ev) => { ev.preventDefault(); lookup(input.value.trim().toUpperCase(), result); },
    },
    h("div", { class: "lookup-row" }, field("Booking reference", input),
      h("div", { class: "field field-action" },
        h("button", { type: "submit", class: "btn btn-primary", testid: "lookup-submit" }, "Find booking"))));
    main.append(
      h("section", { class: "hero" },
        h("p", { class: "eyebrow" }, "Manage a booking"),
        h("h1", { class: "screen-title" }, "Find your reservation"),
        h("p", { class: "lead" }, "Enter the reference from your confirmation to see the details or cancel.")),
      form, result);
  }

  function lookupError(box, message, withLogin) {
    box.replaceChildren(h("p", { class: "notice notice-refused", role: "alert",
      testid: "reservation-error" }, message,
    withLogin ? [" ", link("/login", "Log in")] : null));
  }

  async function lookup(reference, box) {
    const seq = ++lookupSeq;
    if (!currentSession()) {
      lookupError(box, "Log in to look up your reservations.", true);
      return;
    }
    if (!reference) {
      lookupError(box, "Enter your booking reference.");
      return;
    }
    box.replaceChildren(h("p", { class: "loading-text" }, h("span", { class: "spinner", "aria-hidden": "true" }),
      "Looking up your booking…"));
    const found = await api("GET", `/reservations/${encodeURIComponent(reference)}`, { auth: true });
    if (seq !== lookupSeq) return;
    if (found.kind !== "response") {
      lookupError(box, "We couldn't reach Tablekeeper. Please try again.");
      return;
    }
    if (found.status === 401) {
      lookupError(box, "Your session has ended. Please log in again.", true);
      return;
    }
    if (found.status !== 200 || !found.data || typeof found.data.reference !== "string") {
      lookupError(box, `We couldn't find a reservation with reference ${reference} on your account.`);
      return;
    }
    const detail = await api("GET", `/restaurants/${encodeURIComponent(found.data.restaurant_id)}`);
    if (seq !== lookupSeq) return;
    const restaurant = detail.kind === "response" && detail.status === 200 ? detail.data :
      { id: found.data.restaurant_id, name: found.data.restaurant_id, tables: [] };
    showReservation(box, found.data, restaurant, null);
  }

  function showReservation(box, r, restaurant, error) {
    const confirmed = r.status === "confirmed";
    const errorSlot = h("div", { class: "slot" }, error ? h("p", { class: "notice notice-refused",
      role: "alert", testid: "reservation-error" }, error) : null);
    const cancel = confirmed ? h("button", {
      type: "button", class: "btn btn-danger", testid: "reservation-cancel-button",
      onclick: async () => {
        cancel.classList.add("is-loading");
        cancel.setAttribute("aria-busy", "true");
        cancel.textContent = "Cancelling…";
        const result = await api("POST", `/reservations/${encodeURIComponent(r.reference)}/cancel`,
          { auth: true });
        if (result.kind === "response" && result.status === 200 && result.data &&
            typeof result.data.status === "string") {
          showReservation(box, result.data, restaurant, null);
          return;
        }
        const code = result.kind === "response" ? errorCode(result) : null;
        const message = code === "cutoff_passed" ?
          "It's too close to the reservation time to cancel online. Please contact the restaurant." :
          code === "unauthenticated" ? "Your session has ended. Please log in again." :
            result.kind === "response" ? (serverMessage(result) || "This booking couldn't be cancelled.") :
              "We couldn't reach Tablekeeper, so the booking may not be cancelled. Please try again.";
        showReservation(box, r, restaurant, message);
      },
    }, "Cancel reservation") : null;
    box.replaceChildren(errorSlot, h("article", { class: "card reservation-detail",
      testid: "reservation-detail" },
    h("div", { class: "reservation-head" },
      h("div", {},
        h("p", { class: "eyebrow" }, `Reference ${r.reference}`),
        h("h2", { class: "reservation-title" }, restaurant.name || r.restaurant_id)),
      h("span", { class: `badge badge-${confirmed ? "confirmed" : "cancelled"}`,
        testid: "reservation-status" }, r.status)),
    h("dl", { class: "facts" },
      h("div", {}, h("dt", {}, "When"), h("dd", {}, formatLocal(r.starts_at_local))),
      h("div", {}, h("dt", {}, "Tables"), h("dd", { testid: "reservation-tables" },
        tablesLabel(restaurant, reservationTables(r)))),
      h("div", {}, h("dt", {}, "Guests"), h("dd", {}, partyText(r.party_size)))),
    confirmed ? h("div", { class: "reservation-actions" }, cancel) :
      h("p", { class: "muted" }, "This reservation is cancelled and its table has been released.")));
  }

  render();
})();
