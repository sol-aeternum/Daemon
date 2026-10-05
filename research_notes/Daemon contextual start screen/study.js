/* Daemon contextual start screen — standalone design study behaviour.
   Proposal only. No network calls, no storage, no model calls, no timers that
   represent background work. Every fixture below is invented.

   Contract the study demonstrates:
     - ONE composer, relocated by layout between the empty state and a chat.
     - A contextual suggestion immediately submits its own detailed prompt and
       fictional source text in a NEW local demonstration chat.
     - Hover/focus previews the exact prompt, not a rationale for the suggestion.
     - Existing unsent text/files are kept separately, never silently submitted.
     - "Open chat" simulates a fixture conversation; there is no real navigation.
     - Typing freezes suggestions without deleting the unsent draft.
     - Display/preview is not execution; a click submits a request but cannot
       expand permissions or bypass normal action approvals. */

(() => {
  "use strict";

  /* ------------------------------------------------------------------ *
   * Fixtures — fictional. REFERENCE_NOW keeps relative dates stable so
   * screenshots and validation output do not drift.
   * ------------------------------------------------------------------ */
  const REFERENCE_NOW = new Date("2026-10-05T09:00:00Z");

  const CONVERSATIONS = [
    {
      id: "c-release",
      title: "Release notes for 2.4",
      updated: "2026-10-04T09:05:00Z",
      messageCount: 12,
      pinned: true,
      icon: "i-pen",
      turns: [
        {
          role: "user",
          text: "Collect everything that shipped in 2.4 and keep it in one list.",
        },
        {
          role: "assistant",
          text: "Fixture reply. 2.4 contains four shipped items and two deferred ones; the list is in this fictional conversation.",
        },
      ],
    },
    {
      id: "c-svelte",
      title: "Svelte 5 migration research",
      updated: "2026-10-03T16:40:00Z",
      messageCount: 21,
      pinned: false,
      icon: "i-code",
      turns: [
        {
          role: "user",
          text: "Summarise what actually breaks when we move to Svelte 5.",
        },
        {
          role: "assistant",
          text: "Fixture reply. Three runes affect our components, the store contract changed, and the test helpers moved.",
        },
        {
          role: "user",
          text: "Keep the migration notes together, I will use them next sprint.",
        },
      ],
    },
    {
      id: "c-lisbon",
      title: "Lisbon: which apartment?",
      updated: "2026-10-02T18:20:00Z",
      messageCount: 9,
      pinned: false,
      icon: "i-plane",
      turns: [
        {
          role: "user",
          text: "Two apartments in Lisbon, both under 900 a night. Which one?",
        },
        {
          role: "assistant",
          text: "Fixture reply. The Alfama option is 60 a night more expensive but eight minutes closer to the tram.",
        },
      ],
    },
    {
      id: "c-kitchen",
      title: "Kitchen quote comparison",
      updated: "2026-09-30T11:15:00Z",
      messageCount: 6,
      pinned: false,
      icon: "i-home",
      turns: [
        {
          role: "user",
          text: "Three quotes for the kitchen, all wildly different scopes.",
        },
        {
          role: "assistant",
          text: "Fixture reply. Quote B is cheapest but excludes the electrical work; Quote A is the only one with a written warranty.",
        },
      ],
    },
    {
      id: "c-budget",
      title: "Q4 travel budget",
      updated: "2026-09-28T20:00:00Z",
      messageCount: 4,
      pinned: false,
      icon: "i-plane",
      turns: [
        {
          role: "user",
          text: "What can we still spend on travel this quarter?",
        },
        {
          role: "assistant",
          text: "Fixture reply. Roughly 1,400 remains if the shoulder-season plan holds.",
        },
      ],
    },
  ];

   /* Phase 2 fictional candidate pool: ready prompt + one source per action.
      Three are hand-picked for display; the fourth illustrates the bound,
      not an implemented relevance/ranking engine. */
  const TARGET_CANDIDATES = [
    {
      id: "a-lisbon-draft",
      kind: "start",
      source: "c-lisbon",
      icon: "i-plane",
      label: "Compare the two Lisbon apartments again",
      request:
        "Compare the two Lisbon apartments using the attached conversation excerpt. Produce a compact table of nightly rate, walking distance to the tram and cancellation terms. Distinguish stated facts from missing information: the excerpt says both are under 900 a night, Alfama is 60 more per night and eight minutes closer to the tram, but it does not give cancellation terms or exact rates. Do not invent them. Explain the trade-off and ask for any missing details needed for a firm recommendation. Do not book anything.",
      rank: 1,
      heldBack: 0,
    },
    {
      id: "a-svelte-plan",
      kind: "start",
      source: "c-svelte",
      icon: "i-code",
      label: "Turn the Svelte 5 research into a migration checklist",
      request:
        "Use the attached Svelte 5 migration research excerpt to prepare a next-sprint migration checklist. Separate component/rune changes, store compatibility and test-helper updates. The excerpt mentions these categories but does not identify actual components or APIs: flag those gaps rather than making up breaking changes. Propose verification steps and questions to resolve before changing code. This is planning only; do not edit a repository or claim a migration is complete.",
      rank: 2,
      heldBack: 0,
    },
    {
      id: "a-kitchen-suggest",
      kind: "start",
      source: "c-kitchen",
      icon: "i-home",
      label: "Compare the three kitchen quotes side by side",
      request:
        "Compare the three kitchen quotes using the attached conversation excerpt. Organise price, included work, exclusions and warranty into a side-by-side table. Keep the known facts clear: Quote B is cheapest but excludes electrical work, and Quote A is the only one with a written warranty. The excerpt does not supply prices or Quote C's scope, so mark those as unknown and list the documents or details needed to finish the comparison. Do not contact contractors or accept a quote.",
      rank: 3,
      heldBack: 0,
    },
    {
      id: "a-release-draft",
      kind: "start",
      source: "c-release",
      icon: "i-pen",
      label: "Turn the 2.4 release notes into an announcement",
      request:
        "Draft a short announcement for release 2.4 using the attached release-notes excerpt. It says four items shipped and two were deferred, but does not name them. Ask for the actual list before writing specific feature claims; provide an outline meanwhile and clearly distinguish shipped from deferred work. Do not publish the announcement.",
      rank: 4,
      heldBack: 1,
    },
  ];

  const MVP_CANDIDATES = CONVERSATIONS.slice(0, 3).map((conversation) => ({
    id: `mvp-${conversation.id}`,
    kind: "open",
    source: conversation.id,
    icon: conversation.icon,
    label: `Open “${conversation.title}”`,
    rank: CONVERSATIONS.indexOf(conversation) + 1,
    heldBack: 0,
  }));

  const SAMPLE_FILES = [
    { name: "flight-options.pdf", note: "Fictional PDF" },
    { name: "kitchen-quotes.csv", note: "Fictional CSV" },
    { name: "sprint-notes.txt", note: "Fictional text" },
  ];

  const NEW_USER_EXAMPLES = [
    { verb: "Explain", tail: "something you are learning, in plain language" },
    { verb: "Compare", tail: "two options you are choosing between" },
    { verb: "Draft", tail: "a short document you need to send" },
  ];

  const PERSPECTIVES = [
    "Perspective one — the pragmatic reading (fixture)",
    "Perspective two — the sceptical reading (fixture)",
    "Perspective three — the user's own constraint, restated (fixture)",
  ];

  const KIND_META = {
    start: {
      affordance:
        "Immediately starts a new chat using the detailed prompt and source context.",
    },
    open: {
      badge: "Open chat",
      badgeClass: "badge-open",
      affordance:
        "Opens the conversation you already have. No new request is created.",
    },
  };

  /* ------------------------------------------------------------------ *
   * State
   * ------------------------------------------------------------------ */
  const params = new URLSearchParams(window.location.search);
  const state = {
    view: "home",
    study: normalise(params.get("state"), ["target", "mvp", "new", "error"], "target"),
    load: normalise(params.get("load"), ["ready", "loading", "failed"], "ready"),
    theme: normalise(params.get("theme"), ["dark", "light"], "dark"),
    dismissed: new Set(),
    hidden: false,
    chatId: null,
    demoTurns: [],
    attachments: [],
    contexts: [],
    startDraft: null,
    demoDraft: null,
    frozen: false,
    loadTimer: null,
  };

  function normalise(value, allowed, fallback) {
    return allowed.includes(value) ? value : fallback;
  }

  const $ = (selector) => document.querySelector(selector);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };
  const icon = (id, className = "action-icon") => {
    const span = el("span", className);
    span.setAttribute("aria-hidden", "true");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#${id}`);
    svg.appendChild(use);
    span.appendChild(svg);
    return span;
  };

  const refs = {
    html: document.documentElement,
    main: $("#main"),
    viewHome: $("#view-home"),
    viewChat: $("#view-chat"),
    slotHome: $("#slot-home"),
    slotChat: $("#slot-chat"),
    composer: $("#composer"),
    draft: $("#draft"),
    attachments: $("#attachments"),
    contexts: $("#context-attachments"),
    attachButton: $("#attach-button"),
    attachMenu: $("#attach-menu"),
    sendButton: $("#send-button"),
    actions: $("#actions-region"),
    railList: $("#rail-list"),
    railSearch: $("#rail-search"),
    recentsList: $("#recents-list"),
    transcript: $("#transcript"),
    chatTitle: $("#chat-title"),
    chatNote: $("#chat-note"),
    chatBadge: $("#chat-badge"),
    heroHeading: $("#hero-heading"),
    heroGreeting: $("#hero-greeting"),
    toast: $("#toast"),
    toastText: $("#toast-text"),
    toastUndo: $("#toast-undo"),
    toastClose: $("#toast-close"),
    studyState: $("#study-state"),
    studyLoad: $("#study-load"),
    studyTheme: $("#study-theme"),
    studyReset: $("#study-reset"),
    deliberatePerspectives: $("#deliberate-perspectives"),
  };

  /* ------------------------------------------------------------------ *
   * Formatting helpers (deterministic, UTC)
   * ------------------------------------------------------------------ */
  function relativeTime(iso) {
    const days = Math.round((REFERENCE_NOW - new Date(iso)) / 86400000);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 14) return `${days} days ago`;
    return `${Math.floor(days / 7)} weeks ago`;
  }

  function absoluteTime(iso) {
    const date = new Date(iso);
    const months = [
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sep",
      "Oct",
      "Nov",
      "Dec",
    ];
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getUTCDate()} ${months[date.getUTCMonth()]} ${date.getUTCFullYear()}, ${pad(
      date.getUTCHours(),
    )}:${pad(date.getUTCMinutes())} UTC`;
  }

  function conversationById(id) {
    return CONVERSATIONS.find((conversation) => conversation.id === id) || null;
  }

  /* ------------------------------------------------------------------ *
   * Toast
   * ------------------------------------------------------------------ */
  let toastTimer = null;
  let undoHandler = null;

  function showToast(message, onUndo) {
    refs.toastText.textContent = message;
    undoHandler = onUndo || null;
    refs.toastUndo.hidden = !undoHandler;
    refs.toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      refs.toast.hidden = true;
      undoHandler = null;
    }, 9000);
  }

  refs.toastUndo.addEventListener("click", () => {
    const handler = undoHandler;
    undoHandler = null;
    refs.toast.hidden = true;
    window.clearTimeout(toastTimer);
    if (handler) handler();
  });
  refs.toastClose.addEventListener("click", () => {
    refs.toast.hidden = true;
    undoHandler = null;
    window.clearTimeout(toastTimer);
  });

  /* ------------------------------------------------------------------ *
   * Composer
   * ------------------------------------------------------------------ */
  function draftText() {
    return refs.draft.value.replace(/\s+$/, "");
  }

  function resizeDraft() {
    refs.draft.style.height = "auto";
    refs.draft.style.height = `${Math.min(refs.draft.scrollHeight, 148)}px`;
    refs.sendButton.disabled = draftText().length === 0;
  }

  /** The single composer is relocated, never duplicated. */
  function placeComposer(view) {
    const slot = view === "home" ? refs.slotHome : refs.slotChat;
    if (refs.composer.parentElement !== slot) slot.appendChild(refs.composer);
    resizeDraft();
  }

  function setDraft(text) {
    refs.draft.value = text;
    resizeDraft();
    if (!refs.draft.value.trim()) state.frozen = false;
    updateFreeze();
  }

  /** Draft actions append/prefill. Nothing here sends. */
  function appendToDraft(request) {
    const current = refs.draft.value;
    setDraft(current ? `${current}\n${request}` : request);
    refs.draft.focus();
    refs.draft.setSelectionRange(refs.draft.value.length, refs.draft.value.length);
    showToast("Added to the composer. Nothing was sent.", null);
  }

  function captureDraft() {
    return { text: refs.draft.value, attachments: [...state.attachments], contexts: [...state.contexts] };
  }

  function restoreDraft(draft) {
    state.attachments = [...draft.attachments];
    state.contexts = [...draft.contexts];
    renderAttachments();
    renderContexts();
    setDraft(draft.text);
  }

  function startSuggestion(row) {
    const source = conversationById(row.source);
    if (!source || !row.request || state.view !== "home" || state.study !== "target"
        || state.load !== "ready" || state.hidden || state.dismissed.has(row.id)) return;
    // Copy only invented fixture text. This is NOT runtime source resolution,
    // summarisation, permission enforcement or a production attachment format.
    state.startDraft = captureDraft();
    state.contexts = [{
      id: source.id,
      title: source.title,
      turns: source.turns.map((turn) => ({ ...turn })),
    }];
    // A suggestion sends only its own request/context, never the user's unsent
    // composer text or files. Back/New chat restores that separate start draft.
    state.attachments = [];
    state.demoTurns = [];
    state.demoDraft = null;
    closeAttachMenu();
    setDraft(row.request);
    sendLocalDemonstration();
    showToast("Started a new local chat with this prompt and fictional context. No network request was made.", null);
  }

  function contextNode(context, removable) {
    const item = el("li", "context-attachment");
    item.dataset.source = context.id;
    const details = el("details", "context-preview");
    const summary = el("summary", null, `Context: ${context.title} (fixture)`);
    summary.setAttribute("data-affordance", "context-preview");
    details.appendChild(summary);
    const excerpt = el("div", "context-excerpt");
    excerpt.appendChild(el("p", "mock-note", "Attached fictional text, not the whole original chat. No real source was fetched."));
    context.turns.forEach((turn) => {
      excerpt.appendChild(el("p", null, `${turn.role === "user" ? "You" : "Daemon"}: ${turn.text}`));
    });
    details.appendChild(excerpt);
    item.appendChild(details);
    if (removable) {
      const remove = el("button", "attachment-remove", "×");
      remove.type = "button";
      remove.setAttribute("data-affordance", "context-remove");
      remove.setAttribute("aria-label", `Remove source context: ${context.title}`);
      remove.addEventListener("click", () => {
        state.contexts = state.contexts.filter((entry) => entry.id !== context.id);
        renderContexts();
        refs.draft.focus();
      });
      item.appendChild(remove);
    }
    return item;
  }

  function renderContexts() {
    refs.contexts.textContent = "";
    state.contexts.forEach((context) => refs.contexts.appendChild(contextNode(context, true)));
  }

  function renderAttachments() {
    refs.attachments.textContent = "";
    state.attachments.forEach((name) => {
      const item = el("li", "attachment");
      item.appendChild(el("span", "attachment-name", name));
      const remove = el("button", "attachment-remove");
      remove.type = "button";
      remove.setAttribute("data-affordance", "attachment-remove");
      remove.setAttribute("aria-label", `Remove fictional attachment ${name}`);
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      use.setAttribute("href", "#i-x");
      svg.appendChild(use);
      remove.appendChild(svg);
      remove.addEventListener("click", () => {
        state.attachments = state.attachments.filter((item_) => item_ !== name);
        renderAttachments();
        refs.attachButton.focus();
      });
      item.appendChild(remove);
      refs.attachments.appendChild(item);
    });
  }

  function renderAttachMenu() {
    refs.attachMenu.textContent = "";
    SAMPLE_FILES.forEach((file) => {
      const button = el("button", null, `${file.name} — ${file.note}`);
      button.type = "button";
      button.setAttribute("role", "menuitem");
      button.setAttribute("data-affordance", "attach-sample");
      button.addEventListener("click", () => {
        if (!state.attachments.includes(file.name)) {
          state.attachments = [...state.attachments, file.name];
          renderAttachments();
        }
        closeAttachMenu();
        refs.attachButton.focus();
      });
      refs.attachMenu.appendChild(button);
    });
    refs.attachMenu.appendChild(
      el(
        "p",
        "attach-menu-note",
        "Fictional sample files. Nothing is uploaded and nothing leaves this page.",
      ),
    );
  }

  function openAttachMenu() {
    renderAttachMenu();
    refs.attachMenu.hidden = false;
    refs.attachButton.setAttribute("aria-expanded", "true");
    const first = refs.attachMenu.querySelector("button");
    if (first) first.focus();
  }

  function closeAttachMenu() {
    refs.attachMenu.hidden = true;
    refs.attachButton.setAttribute("aria-expanded", "false");
  }

  refs.attachButton.addEventListener("click", () => {
    if (refs.attachMenu.hidden) openAttachMenu();
    else closeAttachMenu();
  });

  refs.attachMenu.addEventListener("keydown", (event) => {
    const items = [...refs.attachMenu.querySelectorAll("button")];
    const index = items.indexOf(document.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      closeAttachMenu();
      refs.attachButton.focus();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next =
        event.key === "ArrowDown"
          ? (index + 1) % items.length
          : (index - 1 + items.length) % items.length;
      items[next].focus();
    }
  });

  document.addEventListener("click", (event) => {
    if (refs.attachMenu.hidden) return;
    if (refs.composer.contains(event.target)) return;
    closeAttachMenu();
  });

  let composing = false;
  refs.draft.addEventListener("compositionstart", () => {
    composing = true;
  });
  refs.draft.addEventListener("compositionend", () => {
    composing = false;
  });
  refs.draft.addEventListener("input", () => {
    resizeDraft();
    // Typing past the override point re-freezes; clearing the draft always
    // restores automatic freezing.
    if (!refs.draft.value.trim()) state.frozen = false;
    updateFreeze();
  });

  refs.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendLocalDemonstration();
  });

  refs.draft.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    if (event.shiftKey) return; // newline
    if (composing || event.isComposing || event.keyCode === 229) return; // IME
    event.preventDefault();
    sendLocalDemonstration();
  });

  function sendLocalDemonstration() {
    const text = draftText();
    if (!text) return;
    state.demoTurns = [
      ...state.demoTurns,
      { role: "user", text, contexts: [...state.contexts] },
      {
        role: "assistant",
        text:
          "Local demonstration only. No model, provider, network call or credential was involved; this reply was written into the page.",
      },
    ];
    state.chatId = "demo";
    state.view = "chat";
    state.attachments = [];
    state.contexts = [];
    renderContexts();
    renderAttachments();
    setDraft("");
    renderView();
    refs.draft.focus();
    showToast("Added to this page only. No request was made.", null);
  }

  /* ------------------------------------------------------------------ *
   * Rail / recents
   * ------------------------------------------------------------------ */
  function renderRail() {
    refs.railList.textContent = "";
    if (state.study === "new" || state.load !== "ready") {
      refs.railList.appendChild(el("li", "rail-empty", state.study === "new"
        ? "No conversations yet."
        : "Conversation history is unavailable in this specimen."));
      return;
    }
    const query = refs.railSearch.value.trim().toLowerCase();
    if (state.demoTurns.length && (!query || "new chat local demonstration".includes(query))) {
      const item = el("li");
      const button = el("button", "rail-item", "New chat (local demonstration)");
      button.type = "button";
      button.setAttribute("data-affordance", "demo-open");
      button.addEventListener("click", () => openConversation("demo", button));
      item.appendChild(button);
      refs.railList.appendChild(item);
    }
    const matches = CONVERSATIONS.filter((conversation) =>
      conversation.title.toLowerCase().includes(query),
    );
    if (!matches.length) {
      refs.railList.appendChild(
        el("li", "rail-empty", "No fictional conversation matches that filter."),
      );
      return;
    }
    matches.forEach((conversation) => {
      const item = el("li");
      const button = el("button", "rail-item");
      button.type = "button";
      button.setAttribute("data-affordance", "rail-open");
      button.setAttribute(
        "aria-current",
        state.view === "chat" && state.chatId === conversation.id ? "true" : "false",
      );
      button.appendChild(icon(conversation.icon, "rail-icon"));
      button.appendChild(el("span", "rail-item-text", conversation.title));
      if (conversation.pinned) button.appendChild(el("span", "rail-pin", "Pinned"));
      button.appendChild(
        el("span", "sr-only", `, last activity ${relativeTime(conversation.updated)}`),
      );
      button.addEventListener("click", () => openConversation(conversation.id, button));
      item.appendChild(button);
      refs.railList.appendChild(item);
    });
  }

  function renderRecents() {
    refs.recentsList.textContent = "";
    if (state.study === "new" || state.load !== "ready") return;
    CONVERSATIONS.forEach((conversation) => {
      const item = el("li");
      const button = el("button", null, conversation.title);
      button.type = "button";
      button.setAttribute("data-affordance", "recents-open");
      button.appendChild(
        el("span", "recents-meta", relativeTime(conversation.updated)),
      );
      button.addEventListener("click", () => openConversation(conversation.id, button));
      item.appendChild(button);
      refs.recentsList.appendChild(item);
    });
  }

  $("#new-chat").addEventListener("click", () => {
    closeAttachMenu();
    backToStart();
    refs.draft.focus();
  });

  refs.railSearch.addEventListener("input", renderRail);

  /* ------------------------------------------------------------------ *
   * Views
   * ------------------------------------------------------------------ */
  let lastChatOpener = null;

  function openConversation(id, opener) {
    if (state.chatId === "demo" && state.view === "chat" && id === "demo") return;
    if (state.startDraft) {
      state.demoDraft = captureDraft();
      restoreDraft(state.startDraft);
      state.startDraft = null;
    }
    if (id === "demo") {
      state.startDraft = captureDraft();
      restoreDraft(state.demoDraft || { text: "", attachments: [], contexts: [] });
    }
    lastChatOpener = opener || null;
    state.chatId = id;
    state.view = "chat";
    closeAttachMenu();
    renderView();
    const heading = $(".chat-head-title");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus();
    }
  }

  function backToStart() {
    const opener = lastChatOpener;
    if (state.startDraft) {
      state.demoDraft = captureDraft();
      restoreDraft(state.startDraft);
      state.startDraft = null;
    }
    state.view = "home";
    renderView();
    const target =
      opener && document.contains(opener) && opener.offsetParent !== null
        ? opener
        : refs.draft;
    if (target) target.focus();
    lastChatOpener = null;
  }

  $("#back-to-start").addEventListener("click", backToStart);

  function renderView() {
    const isChat = state.view === "chat";
    refs.viewChat.hidden = !isChat;
    refs.viewHome.hidden = isChat;
    placeComposer(isChat ? "chat" : "home");
    if (isChat) renderChat();
    renderRail();
    renderRecents();
    renderActions();
    updateFreeze();
  }

  function renderChat() {
    const conversation = state.chatId === "demo" ? null : conversationById(state.chatId);
    refs.transcript.textContent = "";
    if (conversation) {
      refs.chatTitle.textContent = conversation.title;
      refs.chatBadge.hidden = false;
      refs.chatBadge.className = "badge badge-neutral";
      refs.chatBadge.textContent = "Fictional fixture";
      refs.chatNote.textContent =
        "Fictional fixture conversation. In the app this is navigation; nothing was requested here, and your composer draft and attachments are kept.";
      conversation.turns.forEach((turn) => refs.transcript.appendChild(turnNode(turn)));
    } else {
      refs.chatTitle.textContent = "New chat (local demonstration)";
      refs.chatBadge.hidden = false;
      refs.chatBadge.className = "badge badge-draft";
      refs.chatBadge.textContent = "Local demonstration";
      refs.chatNote.textContent =
        "This turn was written into the page. No model, provider, credential or network request was involved.";
      refs.transcript.appendChild(
        el(
          "p",
          "demo-banner",
          "Local demonstration only — phase 2 suggestions are not shipped, and this reply was not produced by a model.",
        ),
      );
      state.demoTurns.forEach((turn) => refs.transcript.appendChild(turnNode(turn)));
    }
  }

  function turnNode(turn) {
    const wrap = el("div", `turn turn-${turn.role}`);
    wrap.appendChild(el("p", "turn-role", turn.role === "user" ? "You" : "Daemon"));
    wrap.appendChild(el("div", "turn-bubble", turn.text));
    if (turn.contexts?.length) {
      const contexts = el("ul", "context-attachments");
      contexts.setAttribute("aria-label", "Source context used in local demonstration");
      turn.contexts.forEach((context) => contexts.appendChild(contextNode(context, false)));
      wrap.appendChild(contexts);
    }
    return wrap;
  }

  /* ------------------------------------------------------------------ *
   * Suggestions / next actions
   * ------------------------------------------------------------------ */
  function visibleCandidates() {
    if (state.study === "mvp") return MVP_CANDIDATES.filter((row) => !state.dismissed.has(row.id));
    return TARGET_CANDIDATES.filter(
      (row) => !row.heldBack && !state.dismissed.has(row.id),
    );
  }

  function heldBackCount() {
    if (state.study !== "target") return 0;
    return TARGET_CANDIDATES.filter((row) => row.heldBack).length;
  }

  function renderActions() {
    const region = refs.actions;
    region.textContent = "";

    if (state.hidden) {
      region.removeAttribute("aria-busy");
      const note = el("div", "status-note status-quiet");
      note.appendChild(
        el(
          "p",
          null,
          "Personal suggestions are hidden. Nothing is derived from your conversations on this screen, and the composer is unaffected.",
        ),
      );
      const again = el("button", "ghost-btn", "Show personal suggestions");
      again.type = "button";
      again.setAttribute("data-affordance", "show-personal");
      again.addEventListener("click", () => {
        state.hidden = false;
        renderActions();
        renderExplain(region);
        showToast("Personal suggestions are visible again.", null);
        const head = region.querySelector(".actions-hide") || region.querySelector("button");
        if (head) head.focus();
      });
      note.appendChild(again);
      region.appendChild(note);
      return;
    }

    if (state.load === "loading") {
      region.setAttribute("aria-busy", "true");
      const note = el("div", "status-note status-loading");
      note.appendChild(
        el(
          "p",
          "status-title",
          "Loading your recent conversations to build suggestions…",
        ),
      );
      const list = el("ul", "skeleton-list");
      [0, 1, 2].forEach(() => list.appendChild(el("li", "skeleton-row")));
      note.appendChild(list);
      note.appendChild(
        el(
          "p",
          "mock-note",
          "Loading state only. The composer above is usable while this resolves.",
        ),
      );
      region.appendChild(note);
      return;
    }
    region.removeAttribute("aria-busy");

    if (state.load === "failed") {
      const note = el("div", "status-note status-error");
      const title = el("p", "status-title");
      title.appendChild(icon("i-alert", "status-icon"));
      title.appendChild(
        el("span", null, "Daemon could not load your recent conversations"),
      );
      note.appendChild(title);
      note.appendChild(
        el(
          "p",
          null,
          "No suggestions are shown. This is not the same as having no history: nothing was deleted and nothing about your conversations changed. Type in the composer and Daemon will still answer.",
        ),
      );
      const retry = el("button", null, "Try loading again");
      retry.type = "button";
      retry.setAttribute("data-affordance", "retry-history");
      retry.addEventListener("click", () => {
        state.load = "loading";
        refs.studyLoad.value = "loading";
        syncUrl();
        renderActions();
        window.setTimeout(() => {
          state.load = "ready";
          refs.studyLoad.value = "ready";
          syncUrl();
          renderActions();
          updateFreeze();
          updateHeading();
          showToast("Mock retry finished. No network call was made.", null);
        }, 320);
      });
      note.appendChild(retry);
      region.appendChild(note);
      return;
    }

    // "New user" has no grounded rows at all. The "History unavailable" story
    // reaches this point only after a successful (mock) retry, where it shows
    // the same grounded rows a ready history would produce.
    if (state.study === "new") {
      renderNewUser(region);
      return;
    }

    const rows = visibleCandidates();
    const head = el("div", "actions-head");
    head.appendChild(
      el(
        "p",
        "actions-title",
        state.study === "target"
          ? "Suggested from your recent conversations"
          : "Recent conversations",
      ),
    );
    const hide = el("button", "actions-hide", "Hide personal suggestions");
    hide.type = "button";
    hide.setAttribute("data-affordance", "hide-personal");
    hide.addEventListener("click", () => {
      state.hidden = true;
      renderActions();
      const focusTarget = region.querySelector("[data-affordance=show-personal]");
      if (focusTarget) focusTarget.focus();
      showToast("Personal suggestions hidden. Nothing was deleted.", null);
    });
    head.appendChild(hide);
    region.appendChild(head);

    if (!rows.length) {
      const note = el(
        "div",
        "status-note status-quiet",
        "You dismissed every suggestion on this screen. Type in the composer, or reset the study state above to bring them back. Dismissal never deletes the source conversation.",
      );
      region.appendChild(note);
      renderExplain(region);
      return;
    }

    const list = el("ul", "action-list");
    list.id = "action-list";
    rows.forEach((row) => list.appendChild(actionRow(row)));
    region.appendChild(list);

    const foot = el("div", "actions-foot");
    const held = heldBackCount();
    foot.appendChild(
      el(
        "p",
        "mock-note",
        state.study === "target"
          ? `${rows.length} of ${TARGET_CANDIDATES.length} candidates shown${
              held
                 ? ` · ${held} held back by the three-action limit`
                : ""
            }. Every source is a fictional fixture.`
          : "Titles and recent activity only — Daemon has no stored next step for these conversations, and none is claimed here.",
      ),
    );
    region.appendChild(foot);
    renderExplain(region);
  }

  let previewsSuppressed = false;

  function actionRow(row) {
    const conversation = conversationById(row.source);
    const meta = KIND_META[row.kind];
    const item = el("li", "action-row");
    item.dataset.row = row.id;

    const main = el("button", "action-main");
    main.type = "button";
    main.setAttribute("data-affordance", `row-${row.kind}`);
    if (row.request) {
      main.setAttribute("aria-label", `${row.label}. Start a new chat immediately.`);
      const preview = el("div", "prompt-tooltip", row.request);
      preview.id = `prompt-${row.id}`;
      preview.setAttribute("role", "tooltip");
      preview.hidden = true;
      main.setAttribute("aria-describedby", preview.id);
      let hideTimer;
      const hidePreview = () => { preview.hidden = true; };
      const showPreview = () => {
        if (previewsSuppressed) return;
        window.clearTimeout(hideTimer);
        hidePromptPreviews();
        preview.hidden = false;
        const rect = item.getBoundingClientRect();
        preview.style.width = `${Math.min(432, window.innerWidth - 24)}px`;
        const height = preview.getBoundingClientRect().height;
        const centredLeft = rect.left + (rect.width - preview.offsetWidth) / 2;
        preview.style.left = `${Math.max(12, Math.min(centredLeft, window.innerWidth - preview.offsetWidth - 12))}px`;
        preview.style.top = `${Math.max(12, Math.min(rect.top - height - 8, window.innerHeight - height - 12))}px`;
      };
      main.addEventListener("mouseenter", showPreview);
      main.addEventListener("focus", () => {
        previewsSuppressed = false;
        showPreview();
      });
      main.addEventListener("keydown", (event) => {
        if (preview.hidden) return;
        const scrollKeys = ["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End"];
        if (!scrollKeys.includes(event.key)) return;
        event.preventDefault();
        if (event.key === "Home") preview.scrollTop = 0;
        else if (event.key === "End") preview.scrollTop = preview.scrollHeight;
        else preview.scrollTop += (event.key.endsWith("Down") ? 1 : -1)
          * (event.key.startsWith("Page") ? preview.clientHeight : 32);
      });
      item.addEventListener("mouseleave", () => { hideTimer = window.setTimeout(hidePreview, 180); });
      main.addEventListener("blur", hidePreview);
      preview.addEventListener("mouseenter", () => window.clearTimeout(hideTimer));
      item.appendChild(preview);
    }
    main.appendChild(icon(row.icon));
    const text = el("span", "action-text");
    text.appendChild(el("span", "action-label", row.label));

    const source = el("span", "action-source");
    if (row.kind === "open") {
      source.appendChild(el("span", `badge ${meta.badgeClass}`, meta.badge));
    }
    if (state.study === "target") {
      source.appendChild(
        el("span", null, `From “${conversation.title}” · last activity ${relativeTime(conversation.updated)}`),
      );
    } else {
      source.appendChild(el("span", null, `last activity ${relativeTime(conversation.updated)}`));
      if (conversation.pinned) {
      source.appendChild(
        el(
          "span",
          `badge ${conversation.pinned ? "badge-pin" : "badge-neutral"}`,
          "Pinned",
        ),
      );
      }
    }
    text.appendChild(source);
    main.appendChild(text);
    main.addEventListener("click", () => {
      if (row.kind === "open") {
        openConversation(row.source, main);
      } else {
        startSuggestion(row);
      }
    });
    item.appendChild(main);

    const tools = el("div", "action-tools");
    const dismiss = el("button", "tool-btn");
    dismiss.type = "button";
    dismiss.setAttribute("data-affordance", "row-dismiss");
    dismiss.setAttribute("aria-label", `Dismiss suggestion: ${row.label}`);
    const x = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const xUse = document.createElementNS("http://www.w3.org/2000/svg", "use");
    xUse.setAttribute("href", "#i-x");
    x.appendChild(xUse);
    dismiss.appendChild(x);
    dismiss.addEventListener("click", () => dismissRow(row));
    tools.appendChild(dismiss);
    item.appendChild(tools);
    return item;
  }

  function hidePromptPreviews() {
    document.querySelectorAll(".prompt-tooltip").forEach((preview) => { preview.hidden = true; });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    // Hiding an overlay can reveal another row under a stationary pointer.
    // Do not let that synthetic mouseenter immediately reopen a tooltip.
    previewsSuppressed = true;
    hidePromptPreviews();
  });
  document.addEventListener("pointermove", () => { previewsSuppressed = false; });
  window.addEventListener("resize", hidePromptPreviews);
  document.addEventListener("scroll", (event) => {
    if (event.target.closest?.(".prompt-tooltip")) return;
    hidePromptPreviews();
  }, true);

  function dismissRow(row) {
    state.dismissed.add(row.id);
    renderActions();
    showToast(`Dismissed “${row.label}”.`, () => {
      state.dismissed.delete(row.id);
      renderActions();
      const restored = document.querySelector(`[data-row="${row.id}"]`);
      const focusable = restored && restored.querySelector("[data-affordance^=row-]");
      if (focusable) focusable.focus();
      showToast("Suggestion restored. The source conversation was never changed.", null);
    });
    const next = refs.actions.querySelector("[data-affordance^=row-]");
    if (next) next.focus();
  }

  function renderNewUser(region) {
    const note = el("div", "status-note status-quiet");
    note.appendChild(
      el(
        "p",
        null,
        "No history yet, so nothing here is personalised. These three examples are ordinary prompts — not modes and not commands.",
      ),
    );
    region.appendChild(note);
    const details = el("details", "explain");
    const summary = el("summary", null, "Not sure where to start? Three examples");
    summary.setAttribute("data-affordance", "examples-toggle");
    details.appendChild(summary);
    const body = el("div", "explain-body");
    const list = el("ul", "new-user-examples");
    NEW_USER_EXAMPLES.forEach((example) => {
      const item = el("li");
      const button = el("button");
      button.type = "button";
      button.setAttribute("data-affordance", "new-user-example");
      button.appendChild(el("span", "ex-verb", `${example.verb} `));
      button.appendChild(el("span", null, example.tail));
      button.addEventListener("click", () => appendToDraft(`${example.verb} ${example.tail}…`));
      item.appendChild(button);
      list.appendChild(item);
    });
    body.appendChild(list);
    details.appendChild(body);
    region.appendChild(details);
    region.appendChild(
      el(
        "p",
        "mock-note",
        "Study mock · all sources fictional · phase 2 next actions are not shipped.",
      ),
    );
  }

  function renderExplain(region) {
    const details = el("details", "explain");
    const summary = el("summary", null, "What these rows do, and what they are not");
    summary.setAttribute("data-affordance", "explain-toggle");
    details.appendChild(summary);
    const body = el("div", "explain-body");
    body.appendChild(
      el(
        "p",
        "explain-item",
        "Suggestions show a task summary. Hover or keyboard focus to read the exact detailed prompt. Clicking or tapping the task immediately submits its prompt and source context in a new chat. There are no Start chat badges or separate Preview buttons. In this study, a click creates a local demonstration only.",
      ),
    );
    body.appendChild(
      el(
        "p",
        "explain-item",
        "Recent conversations — returns you to an existing conversation without submitting a new request. This is navigation, not a contextual suggestion.",
      ),
    );
    body.appendChild(
      el(
        "p",
        "explain-item",
        "Your unrelated unsent draft and files are kept separately, not sent with a suggestion. Back to start or New chat restores them. The contextual prompt is submitted immediately, not staged for another Send.",
      ),
    );
    body.appendChild(
      el(
        "p",
        "explain-item",
        "A click authorises submitting the suggestion's request, not expanding permissions or bypassing normal action approvals. Merely showing or previewing a row does not start work. Context is untrusted source data; incomplete excerpts do not establish missing facts. No real request or background work occurs in this study.",
      ),
    );
    details.appendChild(body);
    region.appendChild(details);
    region.appendChild(
      el(
        "p",
        "mock-note",
        "Study mock · every source is a fictional fixture · phase 2 semantic next steps are not shipped.",
      ),
    );
  }

  function updateFreeze() {
    const list = $("#action-list");
    const shouldFreeze =
      state.frozen === "forced" ? false : refs.draft.value.trim().length > 0;
    state.frozen = shouldFreeze;
    const region = refs.actions;
    let pause = region.querySelector(".actions-paused");
    if (!list) {
      if (pause) pause.remove();
      return;
    }
    if (shouldFreeze) {
      if (list) list.hidden = true;
      if (!pause) {
        pause = el("p", "actions-paused mock-note");
        pause.setAttribute("data-affordance", "actions-paused");
        pause.appendChild(
          document.createTextNode(
            "Suggestions are paused while you draft. Your text is untouched. ",
          ),
        );
        const resume = el("button", "actions-hide", "Show them anyway");
        resume.type = "button";
        resume.setAttribute("data-affordance", "resume-suggestions");
        // Keep the draft and reveal suggestions for this selection. The next
        // edit or view refresh pauses them again.
        resume.addEventListener("click", () => {
          state.frozen = "forced";
          updateFreeze();
          const row = $("#action-list [data-affordance]");
          if (row) row.focus();
        });
        pause.appendChild(resume);
        const foot = region.querySelector(".actions-foot");
        if (foot) region.insertBefore(pause, foot);
        else region.appendChild(pause);
      }
    } else {
      if (list) list.hidden = false;
      if (pause) pause.remove();
    }
  }

  /* ------------------------------------------------------------------ *
   * Auto / Deliberate dialogs
   * ------------------------------------------------------------------ */
  const autoDialog = $("#auto-dialog");
  let autoOpener = null;
  $("#auto-button").addEventListener("click", (event) => {
    autoOpener = event.currentTarget;
    autoDialog.showModal();
    $("#auto-close").focus();
  });
  $("#auto-close").addEventListener("click", () => autoDialog.close());
  autoDialog.addEventListener("close", () => {
    if (autoOpener && document.contains(autoOpener)) autoOpener.focus();
  });

  const deliberateDialog = $("#deliberate-dialog");
  let deliberateOpener = null;
  PERSPECTIVES.forEach((perspective) =>
    refs.deliberatePerspectives.appendChild(el("li", null, perspective)),
  );
  $("#deliberate-button").addEventListener("click", (event) => {
    deliberateOpener = event.currentTarget;
    deliberateDialog.showModal();
    $("#deliberate-close").focus();
  });
  $("#deliberate-close").addEventListener("click", () => deliberateDialog.close());
  deliberateDialog.addEventListener("close", () => {
    if (deliberateOpener && document.contains(deliberateOpener)) deliberateOpener.focus();
  });

  /* ------------------------------------------------------------------ *
   * Study controls
   * ------------------------------------------------------------------ */
  function syncUrl() {
    const query = new URLSearchParams({
      state: state.study,
      load: state.load,
      theme: state.theme,
    });
    window.history.replaceState(null, "", `?${query.toString()}`);
  }

  const studyToggle = $("#study-toggle");
  const studyPanel = $("#study-panel");
  studyToggle.addEventListener("click", () => {
    studyPanel.hidden = !studyPanel.hidden;
    studyToggle.setAttribute("aria-expanded", String(!studyPanel.hidden));
    $("#study-toggle-label").textContent = studyPanel.hidden
      ? "Study controls"
      : "Hide controls";
  });

  function updateHeading() {
    refs.heroHeading.textContent =
      state.study === "new" || state.load === "failed"
        ? "What would you like to do?"
        : "What would you like to pick up?";
  }

  function armLoading() {
    window.clearTimeout(state.loadTimer);
    state.loadTimer = window.setTimeout(() => {
      state.load = "ready";
      refs.studyLoad.value = "ready";
      syncUrl();
      renderActions();
      updateFreeze();
    }, 600);
  }

  refs.studyState.addEventListener("change", () => {
    state.study = refs.studyState.value;
    // The "History unavailable" story implies a failed load; every other story
    // is shown with a working history.
    state.load = state.study === "error" ? "failed" : "ready";
    refs.studyLoad.value = state.load;
    syncUrl();
    if (state.load === "loading") armLoading();
    else window.clearTimeout(state.loadTimer);
    renderActions();
    updateFreeze();
    updateHeading();
    renderRail();
    renderRecents();
  });

  refs.studyLoad.addEventListener("change", () => {
    state.load = refs.studyLoad.value;
    syncUrl();
    if (state.load === "loading") armLoading();
    else window.clearTimeout(state.loadTimer);
    renderActions();
    updateFreeze();
    updateHeading();
    renderRail();
    renderRecents();
  });

  refs.studyTheme.addEventListener("change", () => {
    state.theme = refs.studyTheme.value;
    refs.html.setAttribute("data-theme", state.theme);
    syncUrl();
  });

  refs.studyReset.addEventListener("click", () => {
    state.dismissed.clear();
    state.hidden = false;
    state.study = "target";
    state.load = "ready";
    refs.studyState.value = "target";
    refs.studyLoad.value = "ready";
    syncUrl();
    renderActions();
    updateFreeze();
    refs.studyReset.focus();
    showToast("Study state reset. Your unsent draft was kept.", null);
  });

  /* ------------------------------------------------------------------ *
   * Boot
   * ------------------------------------------------------------------ */
  refs.html.setAttribute("data-theme", state.theme);
  refs.studyState.value = state.study;
  if (state.study === "error") {
    state.load = "failed";
    refs.studyLoad.value = "failed";
  }
  refs.studyTheme.value = state.theme;
  refs.heroGreeting.textContent =
    REFERENCE_NOW.getUTCHours() >= 5 && REFERENCE_NOW.getUTCHours() < 12
      ? "Good morning"
      : REFERENCE_NOW.getUTCHours() < 17
        ? "Good afternoon"
        : "Good evening";
  updateHeading();
  renderAttachments();
  closeAttachMenu();
  renderView();
  if (state.load === "loading") armLoading();
})();
