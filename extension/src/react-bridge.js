/**
 * Runs in the page's own JS world ("world": "MAIN" in the manifest).
 *
 * Workday's prompts are committed through React's `onKeyDown` prop, which
 * React keeps on the input under a `__reactProps$…` key. That key is a JS
 * property of the page's world. A content script runs in an isolated world:
 * it shares the DOM but not the page's JS properties, so from fill.js every
 * input looked prop-less and the Skills picker fell back to typing, which
 * Workday ignores. The harness never showed it because it injects fill.js
 * with a <script> tag, into the page world.
 *
 * Protocol, synchronous both ways (dispatchEvent runs listeners before it
 * returns, whichever world they live in):
 *   the caller tags the input with data-tempoapply-react=<token>, dispatches
 *   "tempoapply:react" on document with a JSON string detail
 *   {token, op: "probe" | "keydown", key?, value?}, and reads the answer
 *   from data-tempoapply-react-result: "ok", "noprops" or "error".
 *
 * Detail is a string because objects do not cross worlds reliably. This only
 * ever calls the page's own handler on the page's own element, so a page
 * script driving it gains nothing it did not already have.
 */
(() => {
  if (window.__tempoapplyReactBridge) return;
  window.__tempoapplyReactBridge = true;

  const TAG = "data-tempoapply-react";
  const RESULT = "data-tempoapply-react-result";

  function reactProps(el) {
    for (const key in el) {
      if (key.startsWith("__reactProps")) return el[key];
    }
    return null;
  }

  document.addEventListener("tempoapply:react", (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.detail);
    } catch (e) {
      return;
    }
    if (!msg || typeof msg.token !== "string") return;
    let el = null;
    try {
      el = document.querySelector(`[${TAG}="${CSS.escape(msg.token)}"]`);
    } catch (e) {
      el = null;
    }
    if (!el) return;

    const props = reactProps(el);
    if (!props || typeof props.onKeyDown !== "function") {
      el.setAttribute(RESULT, "noprops");
      return;
    }
    if (msg.op === "probe") {
      el.setAttribute(RESULT, "ok");
      return;
    }
    if (msg.op !== "keydown") return;
    try {
      props.onKeyDown({
        key: String(msg.key || ""),
        target: { value: String(msg.value == null ? "" : msg.value) },
        preventDefault() {},
        stopPropagation() {},
      });
      el.setAttribute(RESULT, "ok");
    } catch (e) {
      el.setAttribute(RESULT, "error");
    }
  });
})();
