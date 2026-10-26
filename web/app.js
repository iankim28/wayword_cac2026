const state = {
  tab: "talk",
  phase: "ready",
  hearToken: 0,
  reviewToken: 0,
  result: null,
  chosen: null,
  correcting: false,
  placeQuery: "",
  placeMatches: [],
  placeNote: "",
  searchToken: 0,
  working: false,
  error: "",
  history: [],
};

const screen = document.querySelector("#screen");
const recorder = createRecorder({
  onRecording() {
    state.phase = "recording";
    state.error = "";
    render();
  },
  onReview(blob) {
    const token = state.reviewToken;
    state.phase = "ready";
    submitRecording(blob, token);
  },
  onError(message) {
    state.error = message;
    state.phase = "ready";
    state.working = false;
    render();
  },
});

document.querySelector(".tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (state.phase === "recording") {
    recorder.cancel();
    state.phase = "ready";
  }
  if (button.dataset.tab === "history") {
    openHistory();
    return;
  }
  state.tab = button.dataset.tab;
  state.error = state.tab === "talk" ? state.error : "";
  render();
});

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function boot() {
  render();
}

function toggleMic() {
  if (state.phase === "recording") {
    state.hearToken += 1;
    state.reviewToken = state.hearToken;
    state.phase = "stopping";
    state.tab = "result";
    state.working = true;
    state.result = null;
    state.chosen = null;
    state.correcting = false;
    state.placeQuery = "";
    state.placeMatches = [];
    state.placeNote = "";
    state.error = "";
    render();
    recorder.stop();
    return;
  }
  if (state.phase !== "ready") return;
  recorder.start();
}

async function submitRecording(blob, token) {
  try {
    const result = await api("/api/hear?user_id=demo", {
      method: "POST",
      headers: { "Content-Type": "audio/wav" },
      body: blob,
    });
    if (state.hearToken !== token) return;
    state.result = result;
  } catch (error) {
    if (state.hearToken !== token) return;
    state.error = error.message;
  }
  if (state.hearToken !== token) return;
  state.working = false;
  render();
}

async function openHistory() {
  state.tab = "history";
  state.error = "";
  render();
  try {
    const data = await api("/api/history?user_id=demo");
    state.history = data.items;
  } catch (error) {
    state.error = error.message;
  }
  render();
}

async function clearHistory() {
  await api("/api/reset-demo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: "demo" }),
  });
  state.history = [];
  state.chosen = null;
  state.placeNote = "";
  if (state.result) state.result.from_memory = false;
  render();
}

async function chooseCard(card) {
  if (!state.result) return;
  await api("/api/choose", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: "demo",
      heard: state.result.heard,
      place_id: card.id,
    }),
  });
  state.chosen = card;
  state.correcting = false;
  state.placeQuery = "";
  state.placeMatches = [];
  state.placeNote = "";
  render();
}

function openCorrection() {
  state.correcting = true;
  state.placeQuery = "";
  state.placeMatches = [];
  render();
}

async function searchPlaces(query) {
  state.placeQuery = query;
  const token = state.searchToken + 1;
  state.searchToken = token;
  const box = screen.querySelector("#place-matches");
  if (query.trim().length < 2) {
    state.placeMatches = [];
    if (box) box.innerHTML = "";
    return;
  }
  try {
    const data = await api("/api/places?q=" + encodeURIComponent(query.trim()));
    if (state.searchToken !== token) return;
    state.placeMatches = data.places;
  } catch (error) {
    if (state.searchToken !== token) return;
    state.placeMatches = [];
    if (box) box.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
    return;
  }
  paintMatches();
}

function paintMatches() {
  const box = screen.querySelector("#place-matches");
  if (!box) return;
  if (state.placeQuery.trim().length < 2) {
    box.innerHTML = "";
    return;
  }
  if (!state.placeMatches.length) {
    box.innerHTML = `<p class="hint">No place matches that.</p>`;
    return;
  }
  box.innerHTML = state.placeMatches
    .map((place) => `<button type="button" class="card" data-correct="${place.id}">${escapeHtml(place.name)}</button>`)
    .join("");
  box.querySelectorAll("[data-correct]").forEach((button) => {
    button.addEventListener("click", () => {
      const place = state.placeMatches.find((item) => item.id === button.dataset.correct);
      submitCorrection(place);
    });
  });
}

async function submitCorrection(place) {
  if (!state.result || !place) return;
  try {
    await api("/api/correct", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: "demo",
        heard: state.result.heard,
        place_id: place.id,
      }),
    });
  } catch (error) {
    const box = screen.querySelector("#place-matches");
    if (box) box.insertAdjacentHTML("afterbegin", `<p class="error">${escapeHtml(error.message)}</p>`);
    return;
  }
  state.chosen = place;
  state.correcting = false;
  state.placeQuery = "";
  state.placeMatches = [];
  state.placeNote = "will-remember";
  render();
}

function correctionHtml(forceOpen) {
  const open = forceOpen || state.correcting;
  if (!open) return `<button type="button" class="text-action" id="wrong-place">Wrong place?</button>`;
  return `<label class="correct-label" for="place-query">Wrong place?</label>
    <input id="place-query" class="place-query" type="text" value="${escapeHtml(state.placeQuery)}" placeholder="Type the place" autocomplete="off" />
    <div id="place-matches"></div>`;
}

function bindCorrection() {
  const wrong = screen.querySelector("#wrong-place");
  if (wrong) wrong.addEventListener("click", openCorrection);
  const input = screen.querySelector("#place-query");
  if (input) {
    input.addEventListener("input", () => searchPlaces(input.value));
    input.focus();
  }
  paintMatches();
}

function render() {
  document.querySelectorAll(".tabs button").forEach((button) => {
    if (button.dataset.tab === state.tab) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (state.tab === "talk") renderTalk();
  else if (state.tab === "history") renderHistory();
  else renderResult();
}

function renderTalk() {
  const recording = state.phase === "recording";
  const hint = recording ? "Tap again to stop." : "Tap the mic and say a place.";
  screen.innerHTML = `
    <div class="talk-body">
      <h1 class="prompt">Where to?</h1>
      <p class="hint">Near Andover, Massachusetts</p>
      <p class="hint how-to">${hint}</p>
      ${state.error ? `<p class="error">${escapeHtml(state.error)}</p>` : ""}
      <div class="mic-wrap">
        <button type="button" class="mic" id="mic" aria-label="${recording ? "Stop" : "Record"}" ${recording ? 'aria-pressed="true"' : ""}>
          <svg viewBox="0 0 24 24" width="56" height="56" aria-hidden="true">
            <path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z"/>
          </svg>
        </button>
      </div>
    </div>`;
  screen.querySelector("#mic").addEventListener("click", toggleMic);
}

function renderResult() {
  if (state.working) {
    screen.innerHTML = `
      <h1 class="prompt">Listening…</h1>
      <div class="wave" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></div>`;
    return;
  }
  if (state.error) {
    screen.innerHTML = `<h1 class="prompt">Where to?</h1><p class="error">${escapeHtml(state.error)}</p>`;
    return;
  }
  if (!state.result) {
    screen.innerHTML = `<h1 class="prompt">No result yet</h1><p class="hint">Ask for a place on Talk.</p>`;
    return;
  }
  const result = state.result;
  if (state.chosen) {
    renderPlace(state.chosen, result.heard);
    return;
  }
  if (result.outcome === "ask") {
    const cards = result.cards
      .map((card) => `<button type="button" class="card" data-place="${card.id}">${escapeHtml(card.name)}</button>`)
      .join("");
    screen.innerHTML = `
      <p class="kicker">Heard</p>
      <p class="heard">${escapeHtml(result.top_transcript || result.heard)}</p>
      <h1 class="place">Did you mean?</h1>
      ${cards}
      ${correctionHtml(false)}`;
    screen.querySelectorAll("[data-place]").forEach((button) => {
      button.addEventListener("click", () => {
        const card = result.cards.find((item) => item.id === button.dataset.place);
        chooseCard(card);
      });
    });
    bindCorrection();
    return;
  }
  if (result.outcome === "none") {
    screen.innerHTML = `
      <p class="kicker">Heard</p>
      <p class="heard">${escapeHtml(result.top_transcript || result.heard)}</p>
      <h1 class="place">No matching place</h1>
      <p class="caption">Wayword couldn't match that request to a place.</p>
      ${correctionHtml(true)}`;
    bindCorrection();
    return;
  }
  renderPlace(result.place, result.top_transcript || result.heard);
}

function renderPlace(place, heard) {
  const result = state.result || {};
  const comparison =
    result.spelling_label && result.local_label
      ? `<div class="saying ${result.matched_saying === "spelling" ? "match" : ""}"><span>Spelling</span><strong>${escapeHtml(result.spelling_label)}</strong></div>
         <div class="saying ${result.matched_saying === "local" ? "match" : ""}"><span>Local</span><strong>${escapeHtml(result.local_label)}</strong></div>`
      : "";
  const note = state.placeNote
    ? `<p class="caption memory">Wayword <strong>will remember</strong> this next time.</p>`
    : result.from_memory
      ? `<p class="caption memory">Wayword <strong>remembered</strong> this choice.</p>`
      : "";
  screen.innerHTML = `
    <p class="kicker">Heard</p>
    <p class="heard">${escapeHtml(heard || "")}</p>
    ${comparison}
    <h1 class="place">${escapeHtml(place.name)}</h1>
    ${note}
    <iframe class="map-frame" title="Map" src="${mapSrc(place)}"></iframe>
    ${mapLinks(place)}
    ${correctionHtml(false)}`;
  bindCorrection();
}

function renderHistory() {
  const list = state.history.length
    ? `<ul class="history-list">${state.history
        .map((item) => `<li><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.heard)}</small></li>`)
        .join("")}</ul>
       <button type="button" class="text-action" id="clear-history">Clear corrections</button>`
    : `<p class="empty">Nothing here yet. When Wayword asks which place you meant, your choice stays here.</p>`;
  screen.innerHTML = `
    <h1 class="prompt">Places you corrected</h1>
    ${state.error ? `<p class="error">${escapeHtml(state.error)}</p>` : ""}
    ${list}`;
  const clear = screen.querySelector("#clear-history");
  if (clear) clear.addEventListener("click", clearHistory);
}

function mapSrc(place) {
  const lat = place.lat;
  const lon = place.lon;
  const bbox = [lon - 0.08, lat - 0.06, lon + 0.08, lat + 0.06].join(",");
  return `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${lat}%2C${lon}`;
}

function mapLinks(place) {
  const query = encodeURIComponent(place.name);
  const apple = `https://maps.apple.com/?ll=${place.lat},${place.lon}&q=${query}`;
  const google = `https://www.google.com/maps/search/?api=1&query=${query}`;
  return `<a class="map-link" href="${apple}" target="_blank" rel="noopener noreferrer">Start in Maps</a>
          <a class="map-link secondary" href="${google}" target="_blank" rel="noopener noreferrer">Open in Google Maps</a>`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

boot();
