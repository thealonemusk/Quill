/**
 * Service worker: the only place that talks to the backend.
 *
 * Content scripts cannot fetch localhost from a page whose CSP forbids it
 * (Workday's does), so every request is proxied through here. It also routes
 * messages between frames, since a content script cannot address its siblings.
 */

const DEFAULT_API = "http://localhost:8000";
const TIMEOUT_MS = 12000;

async function apiBase() {
  const stored = await chrome.storage.local.get("apiBase");
  return (stored.apiBase || DEFAULT_API).replace(/\/+$/, "");
}

async function call(path, options) {
  const base = await apiBase();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${base}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(options && options.headers) },
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body && body.detail) detail = body.detail;
      } catch (e) {
        /* non-JSON error body */
      }
      return { ok: false, error: detail };
    }
    return { ok: true, data: await response.json() };
  } catch (e) {
    const message = e && e.name === "AbortError" ? "request timed out" : String(e && e.message ? e.message : e);
    return { ok: false, error: message };
  } finally {
    clearTimeout(timer);
  }
}

// Cache the resume per page. Each job may have its own tailored PDF, so one
// cached copy would attach the first job's resume to every later form. The
// frame URL and the tab URL both go to the backend: an embedded Greenhouse
// form lives in an iframe whose URL is not the job's. The TTL is what makes
// swapping in a new resume take effect on its own, without reloading.
const RESUME_TTL_MS = 5 * 60 * 1000;
let resumeCache = new Map();

async function getResume(url, tabUrl) {
  const key = `${url || ""}|${tabUrl || ""}`;
  const hit = resumeCache.get(key);
  if (hit && Date.now() - hit.at < RESUME_TTL_MS) {
    return { ok: true, data: hit.data };
  }
  const query = new URLSearchParams({ url: url || "", tab_url: tabUrl || "" });
  const result = await call(`/api/autofill/resume?${query}`);
  if (result.ok) {
    resumeCache.set(key, { data: result.data, at: Date.now() });
  }
  return result;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    switch (message.type) {
      case "ping":
        sendResponse(await call("/api/autofill/ping"));
        return;

      case "resolve":
        sendResponse(
          await call("/api/autofill/resolve", {
            method: "POST",
            body: JSON.stringify(message.payload),
          })
        );
        return;

      case "resume":
        sendResponse(await getResume(message.url, sender.tab && sender.tab.url));
        return;

      // Tailoring: start a run, then poll it — it takes minutes, and a
      // request here times out in TIMEOUT_MS.
      case "tailor":
        sendResponse(
          await call("/api/autofill/tailor", {
            method: "POST",
            body: JSON.stringify({ ...message.payload, tab_url: (sender.tab && sender.tab.url) || "" }),
          })
        );
        return;

      case "tailorStatus": {
        const result = await call(`/api/autofill/tailor/${encodeURIComponent(message.jobId)}`);
        // A finished run means this page now has its own PDF; the cached
        // default must not be attached by the next Autofill.
        if (result.ok && result.data.state !== "running") resumeCache.clear();
        if (result.ok && result.data.pdf_url) result.data.pdf_url = `${await apiBase()}${result.data.pdf_url}`;
        sendResponse(result);
        return;
      }

      // Workday's application steps show no description, but its posting page
      // did, one click earlier in the same tab. Kept per tab for the session.
      case "rememberJd": {
        const tabId = sender.tab && sender.tab.id;
        if (tabId != null && chrome.storage.session) {
          await chrome.storage.session.set({ [`jd:${tabId}`]: { ...message.jd, url: sender.tab.url, at: Date.now() } });
        }
        sendResponse({ ok: true });
        return;
      }

      case "recallJd": {
        const tabId = sender.tab && sender.tab.id;
        const key = `jd:${tabId}`;
        const stored = tabId != null && chrome.storage.session ? await chrome.storage.session.get(key) : {};
        const jd = stored[key];
        // Same site only: a description remembered from another employer's
        // posting in this tab must not tailor this one's resume.
        const sameSite = jd && sender.tab && new URL(jd.url).hostname === new URL(sender.tab.url).hostname;
        // And recent: an hour-old posting on the same site may be another job.
        const fresh = jd && Date.now() - (jd.at || 0) < 60 * 60 * 1000;
        sendResponse({ ok: true, data: sameSite && fresh ? jd : null });
        return;
      }

      case "track":
        sendResponse(
          await call("/api/autofill/track", {
            method: "POST",
            body: JSON.stringify(message.payload),
          })
        );
        return;

      // Top frame -> every frame in the same tab.
      case "broadcast": {
        const tabId = sender.tab && sender.tab.id;
        if (tabId != null) {
          try {
            await chrome.tabs.sendMessage(tabId, message.message);
          } catch (e) {
            /* frames without our content script simply do not answer */
          }
        }
        sendResponse({ ok: true });
        return;
      }

      // Child frame -> top frame: "I am about to fill, wait for me".
      case "starting": {
        const tabId = sender.tab && sender.tab.id;
        if (tabId != null) {
          try {
            await chrome.tabs.sendMessage(tabId, { type: "TA_FRAME_STARTING" }, { frameId: 0 });
          } catch (e) {
            /* top frame gone */
          }
        }
        sendResponse({ ok: true });
        return;
      }

      // Child frame -> top frame.
      case "report": {
        const tabId = sender.tab && sender.tab.id;
        if (tabId != null) {
          try {
            await chrome.tabs.sendMessage(tabId, { type: "TA_REPORT", result: message.result }, { frameId: 0 });
          } catch (e) {
            /* top frame gone */
          }
        }
        sendResponse({ ok: true });
        return;
      }

      case "setApiBase":
        await chrome.storage.local.set({ apiBase: message.value });
        resumeCache.clear();
        sendResponse({ ok: true });
        return;

      case "getApiBase":
        sendResponse({ ok: true, data: { apiBase: await apiBase() } });
        return;

      case "clearResumeCache":
        resumeCache.clear();
        sendResponse({ ok: true });
        return;

      default:
        sendResponse({ ok: false, error: `unknown message ${message.type}` });
    }
  })();
  return true; // keep the channel open for the async reply
});

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "autofill") return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id == null) return;
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "TA_RUN", opts: {} }, { frameId: 0 });
  } catch (e) {
    /* no content script on this page */
  }
});
