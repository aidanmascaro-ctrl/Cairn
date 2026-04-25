<script lang="ts">
  import { onMount, tick } from "svelte";
  import type { CairnBridge, BriefDraft, BriefFields, BriefJudgeResult } from "./types";

  export let ingestFolder: string | null = null;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "loading" | "chat" | "done" | "editing" | "error";

  let state: State = "idle";
  let draft: BriefDraft | null = null;
  let fields: BriefFields = { filled: 0, total: 8, required_filled: 0, required_total: 5 };
  let messages: Array<{ role: "user" | "assistant"; content: string }> = [];
  let inputText = "";
  let sending = false;
  let errorMsg = "";
  let judge: BriefJudgeResult | null = null;

  let messagesEl: HTMLDivElement | undefined;

  // Edit-mode fields (flat form)
  let editSubjectName = "";
  let editSubjectProfession = "";
  let editNarrativeAngle = "";
  let editTone = "";
  let editNotes = "";

  onMount(async () => {
    if (ingestFolder && cairn) {
      await loadBrief();
    }
  });

  $: if (ingestFolder && cairn) {
    void loadBrief();
  }

  async function loadBrief() {
    if (!cairn || !ingestFolder || state !== "idle") return;
    state = "loading";
    try {
      const result = await cairn.brief.get({ path: ingestFolder });
      draft = result.brief;
      fields = result.fields;
      if (draft?.conversation_log) {
        messages = draft.conversation_log;
      }
      state = draft?.finalised ? "done" : "chat";
      if (state === "chat" && messages.length === 0) {
        await sendGreeting();
      }
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    }
  }

  async function sendGreeting() {
    if (!cairn || !ingestFolder) return;
    sending = true;
    try {
      const result = await cairn.brief.message({
        path: ingestFolder,
        message: "__greeting__",
      });
      messages = [...messages, { role: "assistant", content: result.reply }];
      draft = result.draft;
      fields = result.fields;
      await scrollBottom();
    } catch {
      // Greeting failure is non-fatal — user can still type
    } finally {
      sending = false;
    }
  }

  async function sendMessage() {
    if (!cairn || !ingestFolder || !inputText.trim() || sending) return;
    const text = inputText.trim();
    inputText = "";
    messages = [...messages, { role: "user", content: text }];
    sending = true;
    await scrollBottom();
    try {
      const result = await cairn.brief.message({ path: ingestFolder, message: text });
      messages = [...messages, { role: "assistant", content: result.reply }];
      draft = result.draft;
      fields = result.fields;
      if (result.finalised) {
        judge = result.judge ?? null;
        state = "done";
      }
    } catch (err) {
      messages = [
        ...messages,
        { role: "assistant", content: `⚠ ${err instanceof Error ? err.message : String(err)}` },
      ];
    } finally {
      sending = false;
      await scrollBottom();
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  }

  async function scrollBottom() {
    await tick();
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function startEdit() {
    editSubjectName = draft?.subject?.name ?? "";
    editSubjectProfession = draft?.subject?.profession ?? "";
    editNarrativeAngle = draft?.narrative_angle ?? "";
    editTone = draft?.tone ?? "";
    editNotes = draft?.notes ?? "";
    state = "editing";
  }

  async function saveEdit() {
    if (!cairn || !ingestFolder) return;
    const updates = {
      subject: { name: editSubjectName, profession: editSubjectProfession },
      narrative_angle: editNarrativeAngle,
      tone: editTone,
      notes: editNotes,
    };
    try {
      const result = await cairn.brief.save({ path: ingestFolder, updates });
      draft = result.brief;
      fields = result.fields;
      state = "done";
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
    }
  }

  async function startOver() {
    if (!cairn || !ingestFolder) return;
    await cairn.brief.reset({ path: ingestFolder });
    draft = null;
    messages = [];
    fields = { filled: 0, total: 8, required_filled: 0, required_total: 5 };
    judge = null;
    state = "chat";
    await sendGreeting();
  }

  $: progressPct = fields.total > 0 ? Math.round((fields.filled / fields.total) * 100) : 0;
</script>

{#if ingestFolder !== null}
  <section class="card">
    <h2>Project Brief</h2>

    {#if state === "loading"}
      <p class="hint">Loading…</p>
    {:else if state === "error"}
      <p class="error-msg">✗ {errorMsg}</p>
      <button class="secondary-btn" on:click={loadBrief}>Retry</button>
    {:else if state === "chat"}
      <div class="progress-row">
        <span class="progress-label">{fields.filled}/{fields.total} fields</span>
        <div class="mini-bar-wrap">
          <div class="mini-bar-fill" style="width:{progressPct}%"></div>
        </div>
        <span class="req-label">{fields.required_filled}/{fields.required_total} required</span>
      </div>

      <div class="messages" bind:this={messagesEl}>
        {#each messages as msg, i (i)}
          <div class="msg {msg.role}">
            <span class="msg-text">{msg.content}</span>
          </div>
        {/each}
        {#if sending}
          <div class="msg assistant thinking">
            <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          </div>
        {/if}
      </div>

      <div class="input-row">
        <textarea
          class="chat-input"
          bind:value={inputText}
          on:keydown={handleKeydown}
          placeholder="Type your answer…"
          rows="2"
          disabled={sending}
        ></textarea>
        <button class="send-btn" on:click={sendMessage} disabled={sending || !inputText.trim()}>
          Send
        </button>
      </div>
    {:else if state === "done" && draft !== null}
      <div class="done-block">
        <p class="done-label">Brief complete</p>
        {#if draft.subject}
          <div class="brief-row">
            <span class="brief-key">Subject</span>
            <span class="brief-val">
              {draft.subject.name ?? "—"}
              {draft.subject.profession ? `· ${draft.subject.profession}` : ""}
            </span>
          </div>
        {/if}
        {#if draft.narrative_angle}
          <div class="brief-row">
            <span class="brief-key">Angle</span>
            <span class="brief-val">{draft.narrative_angle}</span>
          </div>
        {/if}
        {#if draft.tone}
          <div class="brief-row">
            <span class="brief-key">Tone</span>
            <span class="brief-val">{draft.tone}</span>
          </div>
        {/if}
        {#if draft.deliverables && draft.deliverables.length > 0}
          <div class="brief-row">
            <span class="brief-key">Deliverables</span>
            <span class="brief-val">{draft.deliverables.map((d) => d.platform).join(", ")}</span>
          </div>
        {/if}
        {#if draft.themes && draft.themes.length > 0}
          <div class="brief-row">
            <span class="brief-key">Themes</span>
            <span class="brief-val">{draft.themes.join(", ")}</span>
          </div>
        {/if}
        {#if draft.privacy}
          <div class="brief-row">
            <span class="brief-key">Privacy</span>
            <span class="brief-val">
              {draft.privacy.cloud_transcription_allowed ? "Cloud OK" : "Local only"}
            </span>
          </div>
        {/if}

        {#if judge && judge.overall_score < 0.7}
          <div class="judge-warn">
            ⚠ Judge confidence {judge.overall_score.toFixed(2)} —
            {#if judge.invented_fields.length > 0}
              invented: {judge.invented_fields.join(", ")}
            {/if}
            {#if judge.missing_fields.length > 0}
              missing: {judge.missing_fields.join(", ")}
            {/if}
          </div>
        {/if}
      </div>

      <div class="btn-row">
        <button class="secondary-btn" on:click={startEdit}>Edit brief</button>
        <button class="secondary-btn" on:click={startOver}>Start over</button>
      </div>
    {:else if state === "editing"}
      <div class="edit-form">
        <label class="field-label"
          >Subject name
          <input type="text" bind:value={editSubjectName} class="text-input" />
        </label>
        <label class="field-label"
          >Profession
          <input type="text" bind:value={editSubjectProfession} class="text-input" />
        </label>
        <label class="field-label"
          >Narrative angle
          <input type="text" bind:value={editNarrativeAngle} class="text-input" />
        </label>
        <label class="field-label"
          >Tone
          <input type="text" bind:value={editTone} class="text-input" />
        </label>
        <label class="field-label"
          >Notes
          <textarea bind:value={editNotes} class="text-input" rows="3"></textarea>
        </label>
        <div class="btn-row">
          <button
            class="secondary-btn"
            on:click={() => {
              state = "done";
            }}>Cancel</button
          >
          <button class="save-btn" on:click={saveEdit}>Save changes</button>
        </div>
      </div>
    {/if}
  </section>
{/if}

<style>
  .card {
    background: #1f1f21;
    border: 1px solid #2a2a2c;
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }
  .card h2 {
    margin: 0 0 0.75rem 0;
    font-size: 1.05rem;
    color: #b9b9b9;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }
  .hint {
    font-size: 0.88rem;
    color: #7f7f82;
    margin: 0 0 0.75rem 0;
  }
  .progress-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }
  .progress-label,
  .req-label {
    font-size: 0.78rem;
    color: #7f7f82;
    flex-shrink: 0;
  }
  .mini-bar-wrap {
    flex: 1;
    height: 4px;
    background: #2a2a2c;
    border-radius: 2px;
    overflow: hidden;
  }
  .mini-bar-fill {
    height: 100%;
    background: #5a8cff;
    border-radius: 2px;
    transition: width 0.3s ease;
  }
  .messages {
    background: #131315;
    border: 1px solid #2a2a2c;
    border-radius: 8px;
    padding: 0.75rem;
    max-height: 280px;
    overflow-y: auto;
    margin-bottom: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .msg {
    max-width: 88%;
    padding: 0.45rem 0.75rem;
    border-radius: 8px;
    font-size: 0.88rem;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.user {
    align-self: flex-end;
    background: #2a3a5a;
    color: #d8e8ff;
    border: 1px solid #3a4a6a;
  }
  .msg.assistant {
    align-self: flex-start;
    background: #252527;
    color: #c8c8cc;
    border: 1px solid #333335;
  }
  .msg.thinking {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0.5rem 0.75rem;
  }
  .dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    background: #5a8cff;
    border-radius: 50%;
    animation: pulse 1.2s ease-in-out infinite;
  }
  .dot:nth-child(2) {
    animation-delay: 0.2s;
  }
  .dot:nth-child(3) {
    animation-delay: 0.4s;
  }
  @keyframes pulse {
    0%,
    80%,
    100% {
      opacity: 0.3;
    }
    40% {
      opacity: 1;
    }
  }
  .input-row {
    display: flex;
    gap: 0.5rem;
    align-items: flex-end;
  }
  .chat-input {
    flex: 1;
    background: #232325;
    border: 1px solid #3a3a3d;
    border-radius: 6px;
    padding: 0.45rem 0.65rem;
    font-size: 0.88rem;
    color: #e6e6e6;
    font-family: inherit;
    resize: none;
    outline: none;
    line-height: 1.5;
  }
  .chat-input:focus {
    border-color: #5a8cff;
  }
  .chat-input:disabled {
    opacity: 0.5;
  }
  .send-btn {
    background: #2a4a8a;
    color: #d8e8ff;
    border: 1px solid #3a5a9a;
    border-radius: 6px;
    padding: 0.45rem 0.9rem;
    font-size: 0.88rem;
    cursor: pointer;
    font-family: inherit;
    flex-shrink: 0;
  }
  .send-btn:hover:not(:disabled) {
    background: #335aaa;
  }
  .send-btn:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .done-block {
    background: #181819;
    border: 1px solid #2a2a2c;
    border-radius: 8px;
    padding: 0.9rem 1rem;
    margin-bottom: 1rem;
  }
  .done-label {
    font-size: 0.78rem;
    color: #50c87a;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin: 0 0 0.6rem 0;
  }
  .brief-row {
    display: flex;
    gap: 0.75rem;
    margin-top: 0.35rem;
    font-size: 0.85rem;
    line-height: 1.4;
  }
  .brief-key {
    color: #7f7f82;
    width: 80px;
    flex-shrink: 0;
    font-size: 0.78rem;
    padding-top: 0.1rem;
  }
  .brief-val {
    color: #c8c8cc;
    flex: 1;
  }
  .judge-warn {
    margin-top: 0.75rem;
    font-size: 0.78rem;
    color: #f0c850;
    line-height: 1.4;
  }
  .btn-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  .secondary-btn {
    background: #3a3a3d;
    color: inherit;
    border: 1px solid #4a4a4d;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
    font-family: inherit;
  }
  .secondary-btn:hover {
    background: #46464a;
  }
  .edit-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
  }
  .field-label {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    font-size: 0.82rem;
    color: #7f7f82;
  }
  .text-input {
    background: #232325;
    border: 1px solid #3a3a3d;
    border-radius: 5px;
    padding: 0.35rem 0.6rem;
    font-size: 0.88rem;
    color: #e6e6e6;
    font-family: inherit;
    outline: none;
    resize: vertical;
  }
  .text-input:focus {
    border-color: #5a8cff;
  }
  .save-btn {
    background: #2a3a5a;
    color: #a8c8ff;
    border: 1px solid #3a4a6a;
    border-radius: 6px;
    padding: 0.35rem 0.8rem;
    font-size: 0.85rem;
    cursor: pointer;
    font-family: inherit;
  }
  .save-btn:hover {
    background: #354a72;
  }
  .error-msg {
    color: #f28b82;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
  }
</style>
