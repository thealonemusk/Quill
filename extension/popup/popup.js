const $ = (id) => document.getElementById(id);

const send = (message) =>
  new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (reply) => {
      if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
      else resolve(reply || { ok: false, error: "no reply" });
    });
  });

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function alertUser(text) {
  $("alert").hidden = !text;
  $("alert").textContent = text || "";
}

async function loadStatus() {
  const base = await send({ type: "getApiBase" });
  if (base.ok) $("api").value = base.data.apiBase;

  const ping = await send({ type: "ping" });
  if (!ping.ok) {
    $("dot").className = "dot down";
    $("subtitle").textContent = "Backend offline";
    alertUser(`Start the backend with "python run.py" — ${ping.error}`);
    $("fill").disabled = true;
    return;
  }

  const data = ping.data;
  $("dot").className = "dot up";
  $("subtitle").textContent = data.ready ? "Ready to fill" : "Profile incomplete";
  $("profile").hidden = false;
  $("p-name").textContent = data.name || "—";
  $("p-email").textContent = data.email || "—";
  $("p-resume").textContent = data.has_resume ? "on file" : "missing";

  if (!data.ready) alertUser(`Profile is missing: ${data.missing.join(", ")}`);
  else if (!data.has_resume) alertUser("No resume on file — upload one in the dashboard Settings.");
  else alertUser("");
}

$("fill").addEventListener("click", async () => {
  const tab = await activeTab();
  if (!tab || tab.id == null) return;
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "TA_SHOW" }, { frameId: 0 });
    await chrome.tabs.sendMessage(tab.id, { type: "TA_RUN", opts: {} }, { frameId: 0 });
    window.close();
  } catch (e) {
    alertUser("This page has no Quill panel — reload it and try again.");
  }
});

$("show").addEventListener("click", async () => {
  const tab = await activeTab();
  if (!tab || tab.id == null) return;
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "TA_SHOW" }, { frameId: 0 });
    window.close();
  } catch (e) {
    alertUser("This page has no Quill panel — reload it and try again.");
  }
});

$("save").addEventListener("click", async () => {
  const value = $("api").value.trim() || "http://localhost:8000";
  await send({ type: "setApiBase", value });
  await loadStatus();
});

loadStatus();
