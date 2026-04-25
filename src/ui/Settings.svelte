<script lang="ts">
  import { onMount } from "svelte";
  import type { CairnBridge, KeyValidateResult } from "./types";

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type Status = "idle" | "saving" | "valid" | "invalid" | "error";

  let anthropicKey = "";
  let assemblyaiKey = "";

  let anthropicStatus: Status = "idle";
  let anthropicError = "";
  let assemblyaiStatus: Status = "idle";
  let assemblyaiError = "";

  onMount(async () => {
    if (!cairn) return;
    const stored = await cairn.keys.get();
    if (stored.anthropic) anthropicKey = stored.anthropic;
    if (stored.assemblyai) assemblyaiKey = stored.assemblyai;
  });

  async function save(type: "anthropic" | "assemblyai") {
    if (!cairn) return;
    const key = type === "anthropic" ? anthropicKey : assemblyaiKey;

    if (type === "anthropic") {
      anthropicStatus = "saving";
      anthropicError = "";
    } else {
      assemblyaiStatus = "saving";
      assemblyaiError = "";
    }

    try {
      await cairn.keys.set({ [type]: key });
      const result: KeyValidateResult = await cairn.keys.validate({ type, key });
      if (type === "anthropic") {
        anthropicStatus = result.valid ? "valid" : "invalid";
        anthropicError = result.error ?? "";
      } else {
        assemblyaiStatus = result.valid ? "valid" : "invalid";
        assemblyaiError = result.error ?? "";
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (type === "anthropic") {
        anthropicStatus = "error";
        anthropicError = msg;
      } else {
        assemblyaiStatus = "error";
        assemblyaiError = msg;
      }
    }
  }

  function statusIcon(s: Status): string {
    if (s === "saving") return "⏳";
    if (s === "valid") return "✅";
    if (s === "invalid" || s === "error") return "❌";
    return "";
  }
</script>

<section class="card">
  <h2>API keys</h2>

  <div class="field">
    <label for="anthropic-key">Anthropic</label>
    <div class="input-row">
      <input
        id="anthropic-key"
        type="password"
        placeholder="sk-ant-…"
        bind:value={anthropicKey}
        autocomplete="off"
        spellcheck={false}
      />
      <button
        on:click={() => save("anthropic")}
        disabled={anthropicStatus === "saving" || !anthropicKey}
      >
        {anthropicStatus === "saving" ? "Saving…" : "Save"}
      </button>
      {#if anthropicStatus !== "idle"}
        <span class="status-icon">{statusIcon(anthropicStatus)}</span>
      {/if}
    </div>
    {#if anthropicError}
      <p class="field-error">{anthropicError}</p>
    {/if}
  </div>

  <div class="field">
    <label for="assemblyai-key">AssemblyAI</label>
    <div class="input-row">
      <input
        id="assemblyai-key"
        type="password"
        placeholder="assemblyai key…"
        bind:value={assemblyaiKey}
        autocomplete="off"
        spellcheck={false}
      />
      <button
        on:click={() => save("assemblyai")}
        disabled={assemblyaiStatus === "saving" || !assemblyaiKey}
      >
        {assemblyaiStatus === "saving" ? "Saving…" : "Save"}
      </button>
      {#if assemblyaiStatus !== "idle"}
        <span class="status-icon">{statusIcon(assemblyaiStatus)}</span>
      {/if}
    </div>
    {#if assemblyaiError}
      <p class="field-error">{assemblyaiError}</p>
    {/if}
  </div>

  <p class="hint">Keys are stored in the OS keychain and never written to disk.</p>
</section>

<style>
  .card {
    background: #1f1f21;
    border: 1px solid #2a2a2c;
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }
  .card h2 {
    margin: 0 0 1rem 0;
    font-size: 1.05rem;
    color: #b9b9b9;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }
  .field {
    margin-bottom: 1rem;
  }
  label {
    display: block;
    font-size: 0.85rem;
    color: #7f7f82;
    margin-bottom: 0.35rem;
  }
  .input-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  input[type="password"] {
    flex: 1;
    background: #131315;
    border: 1px solid #3a3a3d;
    border-radius: 6px;
    color: #e6e6e6;
    font-size: 0.9rem;
    padding: 0.4rem 0.7rem;
    font-family: "SF Mono", ui-monospace, monospace;
    outline: none;
  }
  input[type="password"]:focus {
    border-color: #5a8cff;
  }
  button {
    background: #3a3a3d;
    color: inherit;
    border: 1px solid #4a4a4d;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
    white-space: nowrap;
  }
  button:hover:not(:disabled) {
    background: #46464a;
  }
  button:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .status-icon {
    font-size: 1rem;
    line-height: 1;
  }
  .field-error {
    color: #f28b82;
    font-size: 0.82rem;
    margin: 0.3rem 0 0 0;
  }
  .hint {
    color: #5a5a5e;
    font-size: 0.8rem;
    margin: 0.5rem 0 0 0;
    font-style: italic;
  }
</style>
