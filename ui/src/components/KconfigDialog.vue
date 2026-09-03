<script setup lang="ts">
// The fw.kconfig.* session dialog: one open configuration at a time, driven
// entirely by state.kconfig - see store/agent.ts's applyKconfigMenu. Opened
// by an ActionButton whose method is fw.kconfig.open; closed from here.
import { computed, ref, watch } from "vue";
import {
  clearKconfigSearch,
  closeKconfig,
  closeKconfigHelp,
  kconfigEnter,
  kconfigHelp,
  kconfigReset,
  kconfigSave,
  kconfigSearch,
  kconfigSet,
  kconfigUp,
  state,
} from "../store/agent";
import type { KconfigNode as KconfigNodeType } from "../api/kconfig";
import { mdiMagnify } from "../icons";
import KconfigNode from "./KconfigNode.vue";
import UiDialog from "./UiDialog.vue";
import UiIcon from "./UiIcon.vue";

const query = ref("");
const confirmDiscard = ref(false);
const busy = ref(false);

// `seeded` only rides along on the `open` response - applyKconfigMenu
// spreads a fresh menu on every later call, and none of those carry it, so
// the field itself would vanish from state.kconfig the moment the user
// navigates. Captured here instead, keyed off the session id, so the note
// survives navigating the tree and is dismissed only by the user's own
// click - not by kconfigEnter/Up/Set doing their normal thing.
const seededNote = ref<string[] | null>(null);
watch(
  () => state.kconfig?.session,
  () => {
    seededNote.value = state.kconfig?.seeded?.length
      ? state.kconfig.seeded
      : null;
  },
  // The dialog mounts fresh exactly when a session opens (v-if="state.kconfig"
  // in the parent), so the first "change" this would otherwise wait for has
  // already happened by the time the watcher is created.
  { immediate: true },
);

const dirty = computed(() => state.kconfig?.dirty === true);
const searching = computed(() => state.kconfig?.search != null);
const rows = computed(() => {
  if (!state.kconfig) return [];
  return state.kconfig.search
    ? state.kconfig.search.nodes
    : state.kconfig.nodes;
});
const canGoUp = computed(
  () => !searching.value && (state.kconfig?.breadcrumb.length ?? 0) > 1,
);

async function onEnter(node: KconfigNodeType): Promise<void> {
  query.value = "";
  busy.value = true;
  await kconfigEnter(node.id);
  busy.value = false;
}

async function onSet(node: KconfigNodeType, value: string): Promise<void> {
  busy.value = true;
  await kconfigSet(node.id, value);
  busy.value = false;
}

async function onHelp(node: KconfigNodeType): Promise<void> {
  await kconfigHelp(node.id);
}

async function runSearch(): Promise<void> {
  busy.value = true;
  await kconfigSearch(query.value);
  busy.value = false;
}

function clearSearch(): void {
  query.value = "";
  clearKconfigSearch();
}

/** Climb to an ancestor by going up the right number of times - the
 * breadcrumb has no direct-jump call on the wire. */
async function climbTo(index: number): Promise<void> {
  const depth = (state.kconfig?.breadcrumb.length ?? 1) - 1 - index;
  busy.value = true;
  for (let i = 0; i < depth; i++) {
    await kconfigUp();
  }
  busy.value = false;
}

async function reset(): Promise<void> {
  busy.value = true;
  await kconfigReset();
  busy.value = false;
}

async function save(build: boolean): Promise<void> {
  busy.value = true;
  try {
    if (await kconfigSave(build)) forceClose();
  } finally {
    busy.value = false;
  }
}

// Unsaved edits are the one thing here that cannot be regenerated, so
// closing with edits pending asks first rather than quietly throwing away
// work.
function tryClose(): void {
  if (dirty.value) {
    confirmDiscard.value = true;
    return;
  }
  forceClose();
}

function forceClose(): void {
  confirmDiscard.value = false;
  closeKconfig();
}
</script>

<template>
  <UiDialog
    v-if="state.kconfig"
    dialog-class="kconfig-dialog"
    @close="tryClose"
  >
    <template #header>
      <h2>{{ state.kconfig.type }} / {{ state.kconfig.fw }}</h2>
      <span v-if="dirty" class="chip" data-tone="attention">Unsaved</span>
    </template>

    <p v-if="seededNote" class="alert alert--info kconfig-seeded-note">
      <span>
        Pre-set from {{ state.kconfig.type }}'s recorded chipset:
        {{ seededNote.join(", ") }}.
      </span>
      <button type="button" @click="seededNote = null">Dismiss</button>
    </p>

    <nav class="kconfig-breadcrumb text-caption">
      <template
        v-for="(crumb, index) in state.kconfig.breadcrumb"
        :key="crumb.id"
      >
        <span v-if="index" class="text--disabled">›</span>
        <a
          v-if="index < state.kconfig.breadcrumb.length - 1"
          href="#"
          @click.prevent="climbTo(index)"
        >
          {{ crumb.prompt }}
        </a>
        <strong v-else>{{ crumb.prompt }}</strong>
      </template>
    </nav>

    <div class="kconfig-search">
      <div class="kconfig-search-field">
        <button
          type="button"
          class="kconfig-search-icon"
          title="Search"
          @click="runSearch"
        >
          <UiIcon :path="mdiMagnify" size="small" />
        </button>
        <input
          v-model="query"
          type="search"
          placeholder="Search…"
          @keyup.enter="runSearch"
        />
      </div>
      <button v-if="searching" type="button" @click="clearSearch">
        Back to menu
      </button>
    </div>

    <p v-if="searching" class="muted">
      {{ rows.length }} results for "{{ state.kconfig.search?.query }}"
      <template v-if="state.kconfig.search?.truncated"> (truncated) </template>
    </p>

    <p v-if="!rows.length" class="muted">Nothing here.</p>

    <div class="kconfig-node-list">
      <KconfigNode
        v-for="node in rows"
        :key="node.id"
        :node="node"
        :busy="busy"
        @enter="onEnter"
        @set="onSet"
        @help="onHelp"
      />
    </div>

    <template #actions>
      <button v-if="canGoUp" type="button" :disabled="busy" @click="kconfigUp">
        Up
      </button>
      <button
        type="button"
        class="btn-danger"
        :disabled="!dirty || busy"
        @click="reset"
      >
        Discard
      </button>
      <button type="button" :disabled="!dirty || busy" @click="save(false)">
        Save
      </button>
      <button
        type="button"
        class="btn-primary"
        :disabled="busy"
        @click="save(true)"
      >
        Save & Build
      </button>
    </template>

    <!-- Help arrives as its own payload, so it gets its own overlay rather
         than expanding a row and reflowing the list under the cursor. Two
         nested UiDialogs, not one shared with the confirm-discard overlay
         below: each is its own fixed backdrop, and a later one in the DOM
         already paints above the one it is nested inside without any
         z-index bump - see UiDialog.vue's own note. -->
    <UiDialog
      v-if="state.kconfig.help"
      :title="state.kconfig.help.prompt"
      dialog-class="kconfig-help"
      @close="closeKconfigHelp"
    >
      <pre class="detail-block">{{ state.kconfig.help.help }}</pre>
    </UiDialog>

    <UiDialog
      v-if="confirmDiscard"
      dialog-class="kconfig-confirm"
      @close="confirmDiscard = false"
    >
      <p>Discard unsaved changes to this configuration?</p>
      <template #actions>
        <button type="button" @click="confirmDiscard = false">Cancel</button>
        <button type="button" class="btn-danger" @click="forceClose">
          Discard
        </button>
      </template>
    </UiDialog>
  </UiDialog>
</template>

<style scoped>
/* The actual grid - KconfigNode.vue's own root is `display: contents`, so
   each row's label/control land here as two items in this shared pair of
   columns, which is what makes every row's control line up in one column
   regardless of how wide any single row's own control is. */
/* No `align-items: center` here on purpose: centering would size each grid
   item to its own content height first, so a row whose label wraps (or
   whose control is taller than plain text) would centre its border-bottom
   at a different height than its neighbour in the same row - a doubled,
   staggered separator line instead of one straight one. Stretching (the
   grid default) keeps both cells the same height; KconfigNode.vue's own
   .kconfig-label/.kconfig-control already centre their own content
   vertically within that stretched cell. */
.kconfig-node-list {
  display: grid;
  grid-template-columns: 1fr auto;
  column-gap: 12px;
}

.kconfig-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

/* .alert is already a flex row with a gap - this just pins the button to
   the far side instead of it hugging the text. */
.kconfig-seeded-note button {
  margin-left: auto;
  white-space: nowrap;
}

.kconfig-search {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.kconfig-search-field {
  position: relative;
  flex: 1;
}

.kconfig-search-icon {
  position: absolute;
  left: 4px;
  top: 50%;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.kconfig-search-field input {
  width: 100%;
  padding-left: 32px;
}

.detail-block {
  margin: 2px 0 6px;
  padding: 6px 8px;
  border-radius: 4px;
  background-color: var(--color-inset);
  white-space: pre-wrap;
  font-size: 0.8rem;
}
</style>
