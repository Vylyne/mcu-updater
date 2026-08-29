<script setup lang="ts">
// One row of a fw.kconfig.* menu. Kind decides both what it does on click
// and which control edits it - a menu is a destination, not a value, so it
// gets a button instead of an input.
import { computed } from "vue";
import type { KconfigNode } from "../api/kconfig";
import {
  mdiChevronDown,
  mdiDeveloperBoard,
  mdiHelpCircleOutline,
} from "../icons";
import UiIcon from "./UiIcon.vue";

const props = defineProps<{
  node: KconfigNode;
  busy?: boolean;
}>();

const emit = defineEmits<{
  enter: [node: KconfigNode];
  set: [node: KconfigNode, value: string];
  help: [node: KconfigNode];
}>();

// Shows the bounds inline, because they are conditional and often
// surprising.
const rangeHint = computed(() =>
  props.node.range
    ? `${props.node.range.min}..${props.node.range.max}`
    : undefined,
);

function onCheckbox(event: Event): void {
  const checked = (event.target as HTMLInputElement).checked;
  emit("set", props.node, checked ? "y" : "n");
}

function onSelect(event: Event): void {
  emit("set", props.node, (event.target as HTMLSelectElement).value);
}

function onText(event: Event): void {
  emit("set", props.node, (event.target as HTMLInputElement).value);
}
</script>

<template>
  <div
    class="kconfig-node"
    :data-tone="node.editable === false ? 'unknown' : undefined"
  >
    <template v-if="node.kind === 'menu' || node.enterable">
      <button
        type="button"
        class="kconfig-enter kconfig-span-all"
        :style="{ paddingLeft: `${node.depth * 16}px` }"
        @click="emit('enter', node)"
      >
        <UiIcon :path="mdiDeveloperBoard" size="small" />
        <span class="kconfig-enter-label">{{ node.prompt }}</span>
        <UiIcon
          :path="mdiChevronDown"
          size="small"
          class="kconfig-enter-chevron"
        />
      </button>
    </template>

    <template v-else-if="node.kind === 'comment'">
      <span
        class="muted text-caption kconfig-span-all"
        :style="{ paddingLeft: `${node.depth * 16}px` }"
        >{{ node.prompt }}</span
      >
    </template>

    <template v-else>
      <span
        class="kconfig-label"
        :style="{ paddingLeft: `${node.depth * 16}px` }"
      >
        <span class="kconfig-label-line">
          <span :class="{ 'text--disabled': !node.editable }">{{
            node.prompt
          }}</span>
          <button
            v-if="node.has_help"
            type="button"
            class="btn-icon btn-icon--small kconfig-help-btn"
            title="Help"
            @click="emit('help', node)"
          >
            <UiIcon :path="mdiHelpCircleOutline" size="x-small" />
          </button>
          <!-- `editable` false means kconfiglib will not accept a change:
               another symbol's `select` holds it, or its dependencies are
               unmet. Saying so beats a control that refuses to move. -->
          <span
            v-if="!node.editable"
            class="muted"
            title="Fixed by another setting"
          >
            🔒
          </span>
        </span>
        <span v-if="node.name" class="muted text-caption kconfig-symbol">{{
          node.name
        }}</span>
      </span>

      <span class="kconfig-control">
        <input
          v-if="node.kind === 'bool'"
          type="checkbox"
          class="switch"
          :checked="node.value === 'y'"
          :disabled="!node.editable || busy"
          @change="onCheckbox"
        />

        <!-- A choice shows prompts and sends symbol names; a tristate has
             no prompts, so y/n/m stands for itself. -->
        <select
          v-else-if="node.kind === 'choice'"
          :value="node.value"
          :disabled="!node.editable || busy"
          @change="onSelect"
        >
          <option
            v-for="option in node.options ?? []"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>

        <select
          v-else-if="node.kind === 'tristate'"
          :value="node.value"
          :disabled="!node.editable || busy"
          @change="onSelect"
        >
          <option v-for="value in node.assignable" :key="value" :value="value">
            {{ value }}
          </option>
        </select>

        <!-- int/hex/string. Committed on change, not on every keystroke:
             each set is a round trip that can rewrite the whole menu. -->
        <template v-else>
          <input
            type="text"
            :value="node.value ?? ''"
            :disabled="!node.editable || busy"
            @change="onText"
          />
          <!-- A placeholder only shows on an empty field, and these are
               essentially never empty - the bound stays invisible unless it
               is its own element. -->
          <span v-if="rangeHint" class="muted kconfig-range">{{
            rangeHint
          }}</span>
        </template>
      </span>
    </template>
  </div>
</template>

<style scoped>
/* No box of its own - KconfigDialog.vue's .kconfig-node-list is the actual
   grid, so each row's own children (the label span, the control span) land
   directly in its two shared columns. That sharing is what makes every
   row's control line up in one column instead of each row sizing its own
   control to its own content, the way a plain per-row flex row would. */
.kconfig-node {
  display: contents;
}

/* No divider here - row rhythm comes from padding alone (below). A
   border-bottom on every row read as noisier than the Mainsail-side panel
   it's matching, which spaces its list purely with padding. */
.kconfig-label,
.kconfig-control,
.kconfig-span-all {
  padding: 10px 0;
}

/* Column layout, not a single wrapping flex row: a long prompt used to eat
   line 1 and push the `?` help button and the 🔒 lock onto a line of their
   own, wrapping separately from the text they annotate. Now only
   .kconfig-label-line (prompt + help + lock) wraps together; the CONFIG_FOO
   symbol is a caption on its own line below, never sharing a wrap point
   with them. */
.kconfig-label {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
}

.kconfig-label-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.kconfig-span-all {
  grid-column: 1 / -1;
}

/* A full clickable row, not a link - it used to be a borderless button
   coloured var(--color-primary) (the user's own accent colour), which made
   a submenu entry like "USB ids" shout like a hyperlink next to plain rows.
   Leading device icon, label in the ordinary text colour, trailing chevron
   at the right edge to read as "drill in", the same way a folder or a
   breadcrumb does. */
.kconfig-enter {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  background: none;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.kconfig-enter-label {
  flex: 1;
  min-width: 0;
}

/* Reuses mdiChevronDown rotated -90deg rather than adding a right-pointing
   chevron to icons.ts - same in-repo precedent as
   .panel-collapse-btn[aria-expanded="false"] svg above. */
.kconfig-enter-chevron {
  flex-shrink: 0;
  transform: rotate(-90deg);
}

.kconfig-control {
  display: flex;
  align-items: center;
  justify-self: end;
  gap: 6px;
  width: 100%;
}

/* Each control used to size to its own content, so the shared grid column
   (.kconfig-node-list in KconfigDialog.vue) still left a ragged right edge
   from row to row. .switch is excluded - it's a fixed 34x20px pill and a
   100% width would stretch it out of shape. */
.kconfig-control select,
.kconfig-control input:not(.switch) {
  width: 100%;
}
</style>
