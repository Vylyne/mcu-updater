<script setup lang="ts">
// One row of a fw.kconfig.* menu. Kind decides both what it does on click
// and which control edits it - a menu is a destination, not a value, so it
// gets a button instead of an input.
import { computed } from "vue";
import type { KconfigNode } from "../api/kconfig";

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
    :style="{ paddingLeft: `${node.depth * 16}px` }"
  >
    <template v-if="node.kind === 'menu' || node.enterable">
      <button type="button" class="kconfig-enter" @click="emit('enter', node)">
        {{ node.prompt }} ›
      </button>
    </template>

    <template v-else-if="node.kind === 'comment'">
      <span class="muted text-caption">{{ node.prompt }}</span>
    </template>

    <template v-else>
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
        ?
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
      <span v-if="node.name" class="muted text-caption kconfig-symbol">{{
        node.name
      }}</span>

      <span class="kconfig-control">
        <input
          v-if="node.kind === 'bool'"
          type="checkbox"
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
.kconfig-node {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 3px 0;
  border-bottom: 1px solid var(--color-divider);
}

.kconfig-enter {
  background: none;
  border: none;
  color: var(--color-primary);
  cursor: pointer;
  padding: 2px 0;
  font: inherit;
  text-align: left;
}

.kconfig-control {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
}
</style>
