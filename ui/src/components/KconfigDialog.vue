<script setup lang="ts">
// The fw.kconfig.* session dialog: one open configuration at a time, driven
// entirely by state.kconfig - see store/agent.ts's applyKconfigMenu. Opened
// by an ActionButton whose method is fw.kconfig.open; closed from here.
import { computed, ref } from "vue";
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
import KconfigNode from "./KconfigNode.vue";

const query = ref("");
const confirmDiscard = ref(false);
const busy = ref(false);

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
  await kconfigSave(build);
  busy.value = false;
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
  <div v-if="state.kconfig" class="kconfig-backdrop">
    <div class="kconfig-dialog">
      <header>
        <h2>{{ state.kconfig.type }} / {{ state.kconfig.fw }}</h2>
        <span v-if="dirty" class="chip" data-tone="attention">Unsaved</span>
        <button type="button" @click="tryClose">Close</button>
      </header>

      <nav class="kconfig-breadcrumb">
        <template
          v-for="(crumb, index) in state.kconfig.breadcrumb"
          :key="crumb.id"
        >
          <span v-if="index">›</span>
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
        <input
          v-model="query"
          type="search"
          placeholder="Search…"
          @keyup.enter="runSearch"
        />
        <button type="button" @click="runSearch">Search</button>
        <button v-if="searching" type="button" @click="clearSearch">
          Back to menu
        </button>
      </div>

      <p v-if="searching" class="muted">
        {{ rows.length }} results for "{{ state.kconfig.search?.query }}"
        <template v-if="state.kconfig.search?.truncated"> (truncated)</template>
      </p>

      <p v-if="!rows.length" class="muted">Nothing here.</p>

      <KconfigNode
        v-for="node in rows"
        :key="node.id"
        :node="node"
        :busy="busy"
        @enter="onEnter"
        @set="onSet"
        @help="onHelp"
      />

      <footer>
        <button
          v-if="canGoUp"
          type="button"
          :disabled="busy"
          @click="kconfigUp"
        >
          Up
        </button>
        <button type="button" :disabled="!dirty || busy" @click="reset">
          Discard
        </button>
        <button type="button" :disabled="!dirty || busy" @click="save(false)">
          Save
        </button>
        <button type="button" :disabled="busy" @click="save(true)">
          Save & Build
        </button>
      </footer>

      <!-- Help arrives as its own payload, so it gets its own overlay rather
           than expanding a row and reflowing the list under the cursor. -->
      <div v-if="state.kconfig.help" class="kconfig-backdrop">
        <div class="kconfig-dialog kconfig-help">
          <header>
            <h3>{{ state.kconfig.help.prompt }}</h3>
            <button type="button" @click="closeKconfigHelp">Close</button>
          </header>
          <pre>{{ state.kconfig.help.help }}</pre>
        </div>
      </div>

      <div v-if="confirmDiscard" class="kconfig-backdrop">
        <div class="kconfig-dialog kconfig-confirm">
          <p>Discard unsaved changes to this configuration?</p>
          <button type="button" @click="confirmDiscard = false">Cancel</button>
          <button type="button" @click="forceClose">Discard</button>
        </div>
      </div>
    </div>
  </div>
</template>
