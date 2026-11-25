// First start: the welcome screen (Steam ID), then the Settings banner (match history
// access), then the "older matches" card on Home. Progress lives in the server's
// onboarding_step setting, so closing the app mid-way resumes.
// All text goes in via textContent (h() and clear() from app.js).

import { $, api, clear, h } from "/static/app.js";

let hooks = { onOwnerSet: () => {} };
export let welcomeShown = false;

const masked = (id) => `${id.slice(0, 4)}…${id.slice(-4)}`;

export function initWelcome(opts) {
  hooks = { ...hooks, ...opts };
  $("#welcome-form").addEventListener("submit", (e) => {
    e.preventDefault();
    submitOwner($("#welcome-steam").value, $("#welcome-key").value);
  });
  // a /id/<name> link needs the API key: ask for it right here
  $("#welcome-steam").addEventListener("input", (e) => {
    $("#welcome-key-row").hidden = !/steamcommunity\.com\/id\//i.test(e.target.value);
  });
  $("#onboarding-skip").addEventListener("click", () => setStep("older"));
  $("#older-skip").addEventListener("click", () => setStep("done"));
  $("#older-import").addEventListener("click", () => {
    location.hash = "#settings";
    setTimeout(() => {
      const form = $("#steam-import-form");
      form.scrollIntoView({ behavior: "smooth", block: "center" });
      $("#steam-import-file").focus();
    }, 50);
  });
  $("#owner-notice-close").addEventListener("click", async () => {
    $("#owner-notice").hidden = true;
    try { await api("POST", "/api/owner/notice-seen"); } catch { /* shown again next time */ }
  });
}

/** Show the welcome view instead of every other page (no owner yet). */
export function showWelcome(owner) {
  welcomeShown = true;
  $("#main-nav").hidden = true;
  for (const sec of document.querySelectorAll(".view")) sec.hidden = sec.id !== "view-welcome";
  const cands = (owner && owner.candidates) || [];
  $("#welcome-candidates").hidden = !cands.length;
  clear($("#welcome-candidate-list"), cands.map((c) => h("button.btn-quiet", {
    type: "button", onclick: () => submitOwner(c.steam_id),
  }, `${c.name || "Unknown"} (${masked(c.steam_id)}) · ${c.matches} matches`)));
  $("#welcome-steam").focus();
}

async function submitOwner(input, apiKey) {
  const st = $("#welcome-status");
  st.textContent = "Checking…";
  st.classList.remove("error");
  try {
    const body = { input };
    if (apiKey && apiKey.trim() && !$("#welcome-key-row").hidden) body.api_key = apiKey.trim();
    await api("POST", "/api/owner", body);
    st.textContent = "";
    welcomeShown = false;
    $("#main-nav").hidden = false;
    hooks.onOwnerSet();
  } catch (err) {
    st.textContent = err.message;
    st.classList.add("error");
  }
}

async function setStep(step) {
  try { await api("PUT", "/api/onboarding", { step }); } catch { /* keep the old step */ }
  await renderOnboarding();
}

/** Show the step banners for the current onboarding step and the one-time owner notice. */
export async function renderOnboarding(owner) {
  if (!owner) {
    try { owner = await api("GET", "/api/owner"); } catch { return null; }
  }
  const step = owner.onboarding_step;
  $("#onboarding-banner").hidden = step !== "history";
  $("#older-card").hidden = step !== "older";
  $("#owner-notice").hidden = !owner.notice;
  $("#owner-notice-text").textContent = owner.notice || "";
  return owner;
}

/** Called by app.js after a Test that worked, or a Steam history import that finished. */
export async function advanceAfter(what) {
  let owner;
  try { owner = await api("GET", "/api/owner"); } catch { return; }
  if (what === "test" && owner.onboarding_step === "history") await setStep("older");
  else if (what === "import" && (owner.onboarding_step === "older" || owner.onboarding_step === "history")) await setStep("done");
}
