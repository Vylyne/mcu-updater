<script setup lang="ts">
// The standalone-ui equivalent of the Mainsail fork's <Panel> - see
// mainsail/src/components/ui/Panel.vue. A titled card with a toolbar, an
// optional icon, a #buttons slot for header actions, and an optional
// collapse toggle whose state persists the same way the fork's does (there
// it's a Vuex-backed per-panel key; here it's localStorage, read/written the
// same defensive way App.vue's readStoredApiKey() already is - private
// browsing or disabled storage must not crash the panel, just fail to
// remember).
import { computed, ref } from "vue";
import UiIcon from "./UiIcon.vue";
import { mdiChevronDown } from "../icons";

const props = withDefaults(
  defineProps<{
    title: string;
    icon?: string | null;
    collapsible?: boolean;
    /** localStorage key suffix. Required when collapsible - unique per panel
     * so two collapsible panels don't fight over one stored value. */
    storageKey?: string;
  }>(),
  { icon: null, collapsible: false, storageKey: undefined },
);

const STORAGE_PREFIX = "mcu-updater-ui:panel:";

function readStoredExpanded(): boolean | null {
  if (!props.collapsible || !props.storageKey) return null;
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + props.storageKey);
    return raw === null ? null : raw === "1";
  } catch {
    return null;
  }
}

const expanded = ref(readStoredExpanded() ?? true);

function toggle(): void {
  if (!props.collapsible) return;
  expanded.value = !expanded.value;
  if (!props.storageKey) return;
  try {
    localStorage.setItem(
      STORAGE_PREFIX + props.storageKey,
      expanded.value ? "1" : "0",
    );
  } catch {
    // Private browsing, disabled storage - the toggle still works for this
    // load, it just won't be remembered.
  }
}

const showBody = computed(() => !props.collapsible || expanded.value);
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <h2>
        <UiIcon v-if="icon" :path="icon" size="small" />
        {{ title }}
      </h2>
      <span class="spacer" />
      <span class="buttons">
        <slot name="buttons" />
        <button
          v-if="collapsible"
          type="button"
          class="btn-icon panel-collapse-btn"
          :aria-expanded="expanded"
          @click="toggle"
        >
          <UiIcon :path="mdiChevronDown" size="small" />
        </button>
      </span>
    </div>
    <div v-show="showBody" class="panel-body">
      <slot />
    </div>
  </section>
</template>
