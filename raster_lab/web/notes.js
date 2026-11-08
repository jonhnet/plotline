// Feedback stays on this dev server. Drafts are also kept in this browser until
// saved; each saved note names the exact source/result, not a floating column.
export function createNotes({ onCounts, getView, restoreView }) {
  const $ = (id) => document.getElementById(id);
  let all = [],
    sample = null,
    editing = null,
    requestId = null,
    saving = false,
    loading = false;
  const draftKey = () =>
    sample
      ? `plotline-raster-note:${sample.challenge_id}:${sample.result_key}`
      : null;
  function text(tag, value, className) {
    const el = document.createElement(tag);
    el.textContent = value;
    if (className) el.className = className;
    return el;
  }
  function uuid() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map((v) => v.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function rememberDraft() {
    if (!sample) return;
    try {
      localStorage.setItem(
        draftKey(),
        JSON.stringify({ text: $("comment-text").value, editing, requestId }),
      );
    } catch {
      /* Server saving still works if browser storage is unavailable. */
    }
  }
  function forgetDraft(key) {
    try {
      localStorage.removeItem(key);
    } catch {}
  }
  function buttons() {
    $("comment-text").disabled = saving || loading;
    $("best-in-row").disabled = saving || loading;
    $("save-comment").disabled =
      saving || loading || !sample || !$("comment-text").value.trim();
    $("save-comment").textContent = saving
      ? "Saving…"
      : editing
        ? "Save edit"
        : "Save comment";
    $("cancel-edit").hidden = !editing;
  }
  function count(row, column) {
    return all.filter(
      (n) => n.sample.challenge_id === row && n.sample.experiment_id === column,
    ).length;
  }
  function list() {
    const target = $("comments-list");
    target.replaceChildren();
    if (!sample) return;
    const matches = all.filter(
      (n) =>
        n.sample.challenge_id === sample.challenge_id &&
        n.sample.experiment_id === sample.experiment_id,
    );
    if (!matches.length) {
      target.append(text("p", "No comments yet.", "empty-notes"));
      return;
    }
    for (const note of matches) {
      const article = document.createElement("article");
      article.className = "comment";
      article.dataset.noteId = note.id;
      const earlier = note.sample.result_key !== sample.result_key;
      article.append(
        text(
          "div",
          `${new Date(note.updated_utc).toLocaleString()}${note.version > 1 ? " · edited" : ""}${earlier ? " · earlier version" : ""}`,
          "comment-date",
        ),
      );
      article.append(text("p", note.text, "comment-body"));
      const actions = document.createElement("div");
      actions.className = "comment-actions";
      const edit = text("button", "Edit");
      edit.type = "button";
      edit.onclick = () => {
        editing = { id: note.id, version: note.version };
        requestId = null;
        $("comment-text").value = note.text;
        rememberDraft();
        buttons();
        $("comment-text").focus();
        $("comment-status").textContent = earlier
          ? "Editing a note on an earlier sample version."
          : "Editing saved comment.";
      };
      actions.append(edit);
      if (!earlier) {
        const restore = text("button", `Restore ${note.view.zoom}× view`);
        restore.type = "button";
        restore.onclick = () => restoreView(note.view);
        actions.append(restore);
      }
      const saved = text("a", "Saved sample ↗");
      saved.href = `/comment-samples/${note.sample.result_key}/preview.png`;
      saved.target = "_blank";
      saved.rel = "noopener";
      actions.append(saved);
      article.append(actions);
      target.append(article);
    }
  }
  async function load() {
    const response = await fetch("/api/comments");
    if (!response.ok) throw Error("Could not load comments");
    all = (await response.json()).comments;
    list();
    onCounts();
  }
  function setSample(next) {
    if (
      sample?.challenge_id === next.challenge_id &&
      sample?.result_key === next.result_key
    )
      return;
    rememberDraft();
    sample = structuredClone(next);
    editing = null;
    requestId = null;
    $("comment-target").textContent = next.experiment_name;
    $("comment-text").value = "";
    try {
      const draft = JSON.parse(localStorage.getItem(draftKey()));
      if (draft) {
        $("comment-text").value = draft.text;
        editing = draft.editing;
        requestId = draft.requestId;
      }
    } catch {}
    $("comment-status").textContent = $("comment-text").value
      ? "Unsaved draft restored."
      : "Notes include this sample version and your current view.";
    buttons();
    list();
  }
  $("comment-text").addEventListener("input", () => {
    requestId = null;
    rememberDraft();
    buttons();
  });
  $("best-in-row").onclick = () => {
    $("comment-text").value +=
      ($("comment-text").value ? "\n" : "") + "Best in the row.";
    requestId = null;
    rememberDraft();
    buttons();
    $("comment-text").focus();
  };
  $("cancel-edit").onclick = () => {
    editing = null;
    $("comment-text").value = "";
    forgetDraft(draftKey());
    buttons();
    $("comment-status").textContent =
      "Edit cancelled. Saved comment unchanged.";
  };
  $("reload-comments").onclick = () =>
    load().catch((error) => ($("comment-status").textContent = error.message));
  $("comment-form").onsubmit = async (event) => {
    event.preventDefault();
    if (saving || !sample) return;
    const draft = draftKey(),
      target = structuredClone(sample),
      edit = editing;
    requestId ||= uuid();
    const payload = edit
      ? { text: $("comment-text").value, version: edit.version }
      : {
          id: requestId,
          text: $("comment-text").value,
          sample: target,
          view: { ...getView() },
        };
    rememberDraft();
    saving = true;
    buttons();
    try {
      const response = await fetch(
        edit ? `/api/comments/${edit.id}` : "/api/comments",
        {
          method: edit ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      const result = await response.json();
      if (!response.ok) throw Error(result.error || "Comment was not saved");
      all = [...all.filter((n) => n.id !== result.id), result];
      forgetDraft(draft);
      if (draftKey() === draft) {
        editing = null;
        requestId = null;
        $("comment-text").value = "";
        $("comment-status").textContent = "Saved on the server.";
      }
      list();
      onCounts();
    } catch (error) {
      $("comment-status").textContent =
        `${error.message} Your draft is retained.`;
    } finally {
      saving = false;
      buttons();
    }
  };
  window.addEventListener("pagehide", rememberDraft);
  return {
    load,
    setSample,
    count,
    setLoading: (value) => {
      loading = value;
      buttons();
    },
    focus: () => $("comment-text").focus(),
  };
}
