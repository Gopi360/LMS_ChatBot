/* Course Assistant demo panel.
   All text from the server is untrusted model output. It only ever reaches the
   page through textContent and DOM methods. */
(function () {
  "use strict";

  var CHAT_URL = "/api/v1/chat";
  var HEALTH_URL = "/api/v1/health";
  var MAX_LENGTH = 500;
  var COUNTER_FROM = 400;
  var REQUEST_TIMEOUT_MS = 190000;
  var HEALTH_TIMEOUT_MS = 6000;
  var STILL_READING_AFTER_S = 15;
  var LONG_WAIT_AFTER_S = 60;
  var HEALTH_POLL_BUSY_MS = 5000;
  var HEALTH_POLL_IDLE_MS = 60000;
  var VIEW_KEY = "course-assistant.presenter";
  var NEAR_BOTTOM_PX = 96;

  var STATUS = {
    answered: { title: "Answered" },
    not_found: { title: "Not found in the documents", tone: "honest", icon: "page" },
    out_of_scope: { title: "Outside these courses", tone: "honest", icon: "page" },
    needs_course: { title: "Asked which course", tone: "question", icon: "question" },
    unavailable: { title: "Could not answer", note: "No answer this time", tone: "problem", icon: "alert", retry: true }
  };

  var EMPTY_CARD_TEXT = {
    answered: "This reply did not need a passage from a document.",
    not_found: "No passage answered this, so nothing was made up.",
    out_of_scope: "The question did not match any course, so no document was used.",
    needs_course: "The question could fit several courses, so the assistant asked before searching.",
    unavailable: "The answer step failed, so nothing was shown."
  };

  var el = {
    book: document.documentElement,
    scroller: document.getElementById("scroller"),
    welcome: document.getElementById("welcome"),
    log: document.getElementById("log"),
    form: document.getElementById("composer"),
    input: document.getElementById("input"),
    send: document.getElementById("send"),
    counter: document.getElementById("counter"),
    newChat: document.getElementById("new-chat"),
    presenter: document.getElementById("presenter"),
    jump: document.getElementById("jump"),
    service: document.getElementById("service"),
    serviceLabel: document.getElementById("service-label"),
    serviceDetail: document.getElementById("service-detail"),
    announcer: document.getElementById("announcer"),
    starters: document.getElementById("starters")
  };

  var state = {
    sessionId: null,
    pending: null,
    questionCount: 0,
    stickToBottom: true,
    isLocal: false
  };

  /* Storage: every read and write can throw in a private window. */

  function readStore(store, key) {
    try { return window[store].getItem(key); } catch (error) { return null; }
  }

  function writeStore(store, key, value) {
    try { window[store].setItem(key, value); } catch (error) { /* Memory is enough. */ }
  }

  function makeSessionId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    // randomUUID needs a secure context, so a plain http LAN address lands here.
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (byte) {
      return byte.toString(16).padStart(2, "0");
    }).join("");
  }

  /* A new session on every load: the screen starts empty, so the bot must not remember a course. */
  function startNewSession() {
    state.sessionId = makeSessionId();
  }

  /* Small DOM helpers. */

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function makeIcon(name) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 20 20");
    svg.setAttribute("aria-hidden", "true");
    var shapes = {
      page: "M4 2h8l4 4v12H4zM7 9h6M7 12h6M7 15h3",
      question: "M6.5 7a3.5 3.5 0 1 1 5.2 3c-1 .6-1.7 1.3-1.7 2.5M10 16v.5",
      alert: "M10 2.5 18.5 17h-17zM10 8v4.5M10 15v.5"
    };
    var path = document.createElementNS(ns, "path");
    path.setAttribute("d", shapes[name]);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    return svg;
  }

  function announce(text) {
    el.announcer.textContent = text;
  }

  /* Text rendering. Lines starting "- " or "* " become real list items. */

  function splitLines(text) {
    return String(text).replace(/\r\n?/g, "\n").replace(/^\n+|\n+$/g, "").split("\n");
  }

  function bulletText(line) {
    var match = /^\s*[-*]\s+(.*)$/.exec(line);
    return match ? match[1] : null;
  }

  function renderLines(container, text, fillLine) {
    var list = null;
    splitLines(text).forEach(function (line) {
      var item = bulletText(line);
      if (item !== null) {
        if (!list) {
          list = make("ul");
          container.appendChild(list);
        }
        var li = make("li");
        fillLine(li, item);
        list.appendChild(li);
        return;
      }
      list = null;
      var row = make("p", "answer-line");
      fillLine(row, line);
      container.appendChild(row);
    });
  }

  function fillPlain(node, text) {
    node.textContent = text;
  }

  /* Cited text. A mark covers the sentence just before its [n] marker. Only
     numbers that match a real source count, so "[2026]" stays in a title. */

  function appendCited(node, text, sourceNumbers) {
    var pattern = /\s*\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g;
    var cursor = 0;
    var match;

    function addMarked(chunk, numbers) {
      var trimmed = chunk.replace(/\s+$/, "");
      var boundary = -1;
      var finder = /[.!?]\s+/g;
      var found;
      while ((found = finder.exec(trimmed)) !== null) {
        boundary = found.index + found[0].length;
      }
      if (boundary > 0) {
        node.appendChild(document.createTextNode(trimmed.slice(0, boundary)));
        trimmed = trimmed.slice(boundary);
      }
      if (trimmed) { node.appendChild(make("mark", "", trimmed)); }
      numbers.forEach(function (number) {
        var sup = make("sup", "cite", String(number));
        node.appendChild(sup);
      });
    }

    while ((match = pattern.exec(text)) !== null) {
      var numbers = match[1].split(",").map(function (part) { return parseInt(part, 10); });
      var known = numbers.every(function (number) { return sourceNumbers.indexOf(number) !== -1; });
      if (!known) { continue; }
      var chunk = text.slice(cursor, match.index);
      if (chunk.trim()) {
        addMarked(chunk, numbers);
      } else {
        numbers.forEach(function (number) { node.appendChild(make("sup", "cite", String(number))); });
      }
      cursor = match.index + match[0].length;
    }
    if (cursor < text.length) {
      node.appendChild(document.createTextNode(text.slice(cursor)));
    }
  }

  function pagesText(source) {
    var start = source.page;
    var end = source.page_end;
    if (start === null || start === undefined) { return ""; }
    if (end === null || end === undefined || end === start) { return "page " + start; }
    return "pages " + start + "-" + end;
  }

  function secondsText(seconds) {
    if (typeof seconds !== "number" || !isFinite(seconds)) { return ""; }
    return seconds.toFixed(1) + " s";
  }

  function cardTitle(status, count) {
    var title = (STATUS[status] || STATUS.answered).title;
    if (status === "answered" && count > 0) {
      return title + " from " + count + (count === 1 ? " passage" : " passages");
    }
    return title;
  }

  function buildSource(source) {
    var row = make("li", "source");
    row.appendChild(make("span", "source-number", String(source.number)));

    var body = make("div");
    body.appendChild(make("p", "source-course", source.course_name || "Course document"));
    if (source.section) { body.appendChild(make("p", "source-section", source.section)); }
    if (source.document) { body.appendChild(make("p", "source-document", source.document)); }
    row.appendChild(body);

    var pages = pagesText(source);
    if (pages) { row.appendChild(make("span", "source-page", pages)); }
    return row;
  }

  function buildEvidence(status, sources, seconds) {
    var wrap = make("div", "evidence");
    var clip = make("div", "evidence-clip");
    var card = make("section", "card");
    card.setAttribute("aria-label", "Where this reply came from");

    var head = make("div", "card-head");
    head.appendChild(make("p", "card-title", cardTitle(status, sources.length)));
    var time = secondsText(seconds);
    if (time) { head.appendChild(make("p", "card-time", time)); }
    card.appendChild(head);

    if (sources.length) {
      var list = make("ol");
      sources.forEach(function (source) { list.appendChild(buildSource(source)); });
      card.appendChild(list);
    } else {
      card.appendChild(make("p", "card-empty", EMPTY_CARD_TEXT[status] || EMPTY_CARD_TEXT.answered));
    }

    clip.appendChild(card);
    wrap.appendChild(clip);
    return wrap;
  }

  /* Entries. */

  function createEntry(question) {
    state.questionCount += 1;
    var entry = make("article", "entry");

    var number = make("div", "entry-number", String(state.questionCount));
    number.setAttribute("aria-hidden", "true");
    entry.appendChild(number);

    var asked = make("p", "question");
    asked.appendChild(make("span", "visually-hidden", "You asked: "));
    asked.appendChild(document.createTextNode(question));
    entry.appendChild(asked);

    var reply = make("div", "reply");
    entry.appendChild(reply);
    el.log.appendChild(entry);
    return { node: entry, reply: reply, question: question };
  }

  function clearReply(entry) {
    entry.reply.replaceChildren();
    entry.reply.removeAttribute("data-tone");
    var old = entry.node.querySelector(".evidence");
    if (old) { old.remove(); }
  }

  function showWaiting(entry) {
    clearReply(entry);
    var box = make("div", "waiting");
    // The live announcer speaks for this block, so the log must not read the ticking count.
    box.setAttribute("aria-hidden", "true");

    var reader = make("div", "reader");
    reader.appendChild(make("span"));
    reader.appendChild(make("span"));
    reader.appendChild(make("span"));
    box.appendChild(reader);

    var words = make("div");
    var title = make("p", "waiting-title", "Reading the course documents");
    var time = make("p", "waiting-time", "0 seconds so far");
    words.appendChild(title);
    words.appendChild(time);
    box.appendChild(words);

    entry.reply.appendChild(box);
    announce("Reading the course documents.");
    return { title: title, time: time };
  }

  function tickWaiting(view, seconds) {
    view.time.textContent = seconds + (seconds === 1 ? " second so far" : " seconds so far");
    if (seconds === STILL_READING_AFTER_S) {
      view.title.textContent = "Still reading the course documents";
      announce("Still reading the course documents.");
    } else if (seconds === LONG_WAIT_AFTER_S) {
      view.title.textContent = "This is a long one, still working on it";
      announce("Still working on it.");
    }
  }

  function addNote(entry, info) {
    var note = make("p", "note");
    note.appendChild(makeIcon(info.icon));
    note.appendChild(document.createTextNode(info.note));
    entry.reply.appendChild(note);
  }

  function addRetry(entry) {
    var actions = make("div", "reply-actions");
    var button = make("button", "retry", "Retry");
    button.type = "button";
    button.disabled = Boolean(state.pending);
    button.addEventListener("click", function () {
      if (state.pending) { return; }
      runQuestion(entry);
    });
    actions.appendChild(button);
    entry.reply.appendChild(actions);
  }

  function showReply(entry, data) {
    clearReply(entry);
    var status = STATUS[data.status] ? data.status : "answered";
    var info = STATUS[status];
    var sources = Array.isArray(data.sources) ? data.sources : [];
    var answer = typeof data.answer === "string" && data.answer.trim()
      ? data.answer
      : "No text came back for this question.";

    if (info.tone) { entry.reply.dataset.tone = info.tone; }
    if (info.note) {
      addNote(entry, info);
    } else if (info.icon) {
      entry.reply.appendChild(makeIcon(info.icon));
    }

    var body = make("div", "reply-body");
    entry.reply.appendChild(body);

    var clean = make("div", "answer answer-clean");
    renderLines(clean, answer, fillPlain);
    body.appendChild(clean);

    var cited = typeof data.cited_answer === "string" ? data.cited_answer : "";
    if (cited.trim() && sources.length) {
      var numbers = sources.map(function (source) { return source.number; });
      var marked = make("div", "answer answer-cited");
      renderLines(marked, cited, function (node, line) { appendCited(node, line, numbers); });
      body.appendChild(marked);
      clean.classList.add("has-cited");
    }

    if (info.retry) { addRetry(entry); }
    entry.node.appendChild(buildEvidence(status, sources, data.seconds));
  }

  function showFailure(entry, message) {
    clearReply(entry);
    entry.reply.dataset.tone = "problem";
    addNote(entry, { icon: "alert", note: "No answer this time" });
    entry.reply.appendChild(make("p", "answer", message));
    addRetry(entry);
    announce(message);
  }

  /* Scrolling. */

  function distanceFromBottom() {
    var box = el.scroller;
    return box.scrollHeight - box.scrollTop - box.clientHeight;
  }

  function scrollToEnd() {
    el.scroller.scrollTop = el.scroller.scrollHeight;
  }

  // A reply taller than the screen is shown from its start, so it reads top down.
  function revealEntry(entry) {
    var box = el.scroller;
    var top = entry.node.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    if (entry.node.offsetHeight > box.clientHeight) {
      box.scrollTop = top;
    } else {
      scrollToEnd();
    }
  }

  function afterReply(entry) {
    if (state.stickToBottom) {
      revealEntry(entry);
    } else {
      el.jump.hidden = false;
      el.jump.onclick = function () {
        el.jump.hidden = true;
        state.stickToBottom = true;
        revealEntry(entry);
      };
    }
  }

  /* Sending. */

  function setRetryButtonsDisabled(disabled) {
    el.log.querySelectorAll(".retry").forEach(function (button) { button.disabled = disabled; });
  }

  function refreshSend() {
    el.send.disabled = Boolean(state.pending) || !el.input.value.trim();
  }

  function focusInput() {
    if (window.matchMedia("(pointer: coarse)").matches) { return; }
    el.input.focus();
  }

  function failureMessage(kind, httpStatus) {
    if (kind === "timeout") {
      return "That took too long, so it was stopped. Retry, or ask a shorter question.";
    }
    if (kind === "network") {
      return "The assistant could not be reached. Check that it is running, then retry.";
    }
    if (httpStatus === 400 || httpStatus === 422) {
      return "That question could not be sent. Reword it, then retry.";
    }
    if (httpStatus === 429) {
      return "Too many questions at once. Wait a moment, then retry.";
    }
    if (httpStatus === 503) {
      return "The assistant is not ready yet. Wait a moment, then retry.";
    }
    return "Something went wrong on the assistant's side. Retry the question.";
  }

  async function postQuestion(question, signal) {
    var response = await fetch(CHAT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, message: question }),
      signal: signal
    });
    if (!response.ok) {
      return { failed: failureMessage("http", response.status) };
    }
    var data = await response.json();
    if (!data || typeof data !== "object") {
      return { failed: failureMessage("http", 500) };
    }
    return { data: data };
  }

  async function runQuestion(entry) {
    var controller = new AbortController();
    var request = { controller: controller, timedOut: false, cancelled: false };
    var view = showWaiting(entry);
    var started = Date.now();

    state.pending = request;
    setRetryButtonsDisabled(true);
    refreshSend();
    if (state.stickToBottom) { scrollToEnd(); }

    var ticker = window.setInterval(function () {
      tickWaiting(view, Math.floor((Date.now() - started) / 1000));
    }, 1000);
    var timeout = window.setTimeout(function () {
      request.timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);

    var result;
    try {
      result = await postQuestion(entry.question, controller.signal);
    } catch (error) {
      result = { failed: failureMessage(request.timedOut ? "timeout" : "network") };
    } finally {
      window.clearInterval(ticker);
      window.clearTimeout(timeout);
    }

    if (request.cancelled) { return; }

    state.pending = null;
    if (result.data) {
      showReply(entry, result.data);
      announce("Reply received.");
    } else {
      showFailure(entry, result.failed);
      checkHealth();
    }
    setRetryButtonsDisabled(false);
    refreshSend();
    afterReply(entry);
    focusInput();
  }

  function ask(question) {
    var text = question.trim().slice(0, MAX_LENGTH);
    if (!text || state.pending) { return false; }
    el.welcome.hidden = true;
    state.stickToBottom = true;
    el.jump.hidden = true;
    runQuestion(createEntry(text));
    return true;
  }

  function submitFromInput() {
    if (ask(el.input.value)) {
      el.input.value = "";
      syncInput();
    }
  }

  function resetChat() {
    if (state.pending) {
      state.pending.cancelled = true;
      state.pending.controller.abort();
      state.pending = null;
    }
    el.log.replaceChildren();
    el.welcome.hidden = false;
    el.jump.hidden = true;
    state.questionCount = 0;
    state.stickToBottom = true;
    el.scroller.scrollTop = 0;
    el.input.value = "";
    syncInput();
    startNewSession();
    announce("New chat started.");
    focusInput();
  }

  /* Input. */

  function syncInput() {
    var input = el.input;
    input.style.height = "auto";
    input.style.height = input.scrollHeight + 3 + "px";

    var length = input.value.length;
    el.counter.hidden = length < COUNTER_FROM;
    el.counter.textContent = length + " of " + MAX_LENGTH;
    el.counter.dataset.full = String(length >= MAX_LENGTH);
    refreshSend();
  }

  /* Presenter view. */

  function setPresenter(on) {
    el.book.dataset.view = on ? "presenter" : "student";
    el.presenter.checked = on;
    el.serviceDetail.hidden = !on || !el.serviceDetail.textContent;
  }

  /* Service status. */

  var healthTimer = null;

  function setService(mode, label, detail) {
    el.service.dataset.state = mode;
    if (el.serviceLabel.textContent !== label) { el.serviceLabel.textContent = label; }
    el.serviceDetail.textContent = detail || "";
    el.serviceDetail.hidden = !detail || el.book.dataset.view !== "presenter";
  }

  function scheduleHealth(delay) {
    window.clearTimeout(healthTimer);
    healthTimer = window.setTimeout(checkHealth, delay);
  }

  async function checkHealth() {
    var controller = new AbortController();
    var timeout = window.setTimeout(function () { controller.abort(); }, HEALTH_TIMEOUT_MS);
    try {
      var response = await fetch(HEALTH_URL, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) { throw new Error("health " + response.status); }
      var health = await response.json();
      applyHealth(health);
    } catch (error) {
      setService("offline", "Offline", "");
      scheduleHealth(HEALTH_POLL_BUSY_MS);
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function applyHealth(health) {
    state.isLocal = health.provider === "ollama";
    var parts = [];
    if (health.model) { parts.push("Model: " + health.model); }
    if (typeof health.index_chunks === "number") {
      parts.push(health.index_chunks + " passages, " + health.courses + " courses");
    }
    var detail = parts.join("\n");

    if (health.warming) {
      setService("warming", "Getting ready", detail);
      scheduleHealth(HEALTH_POLL_BUSY_MS);
    } else if (health.status !== "ok" || health.llm_ready === false) {
      setService("degraded", "Not fully ready", detail);
      scheduleHealth(HEALTH_POLL_BUSY_MS);
    } else {
      setService("ready", state.isLocal ? "Ready, running on this laptop" : "Ready", detail);
      scheduleHealth(HEALTH_POLL_IDLE_MS);
    }
  }

  /* Wiring. */

  el.form.addEventListener("submit", function (event) {
    event.preventDefault();
    submitFromInput();
  });

  el.input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submitFromInput();
    }
  });

  el.input.addEventListener("input", syncInput);
  el.newChat.addEventListener("click", resetChat);

  el.presenter.addEventListener("change", function () {
    setPresenter(el.presenter.checked);
    writeStore("localStorage", VIEW_KEY, el.presenter.checked ? "on" : "off");
  });

  el.starters.addEventListener("click", function (event) {
    var button = event.target.closest(".starter");
    if (button) { ask(button.textContent); }
  });

  el.scroller.addEventListener("scroll", function () {
    state.stickToBottom = distanceFromBottom() < NEAR_BOTTOM_PX;
    if (state.stickToBottom) { el.jump.hidden = true; }
  }, { passive: true });

  startNewSession();
  setPresenter(readStore("localStorage", VIEW_KEY) === "on");
  syncInput();
  checkHealth();
})();
