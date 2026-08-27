<script setup lang="ts">
// A generalised .dialog-backdrop/.dialog pair - what used to be duplicated
// inline as .kconfig-backdrop/.kconfig-dialog in KconfigDialog.vue and
// AddMcuWizard.vue. Kept as two plain fixed-position elements (not a
// <dialog>/teleport) rather than folded away: KconfigDialog nests a second
// and third instance of this *inside* itself for the help and discard-
// confirm overlays, and each one needs the same fixed, centered stacking to
// land above its parent - which falls out for free here because later
// position:fixed siblings paint over earlier ones at the same z-index, so a
// nested UiDialog rendered later in the same subtree already stacks above
// the one around it without any bump in z-index.
import UiIcon from "./UiIcon.vue";
import { mdiClose } from "../icons";

withDefaults(
  defineProps<{
    title?: string | null;
    /** Extra class on the inner .dialog, for a caller that wants to cap its
     * width differently (KconfigDialog's help/confirm overlays are narrower
     * than its main session view). */
    dialogClass?: string | null;
  }>(),
  { title: null, dialogClass: null },
);

const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <div class="dialog-backdrop">
    <div class="dialog" :class="dialogClass">
      <div v-if="title !== null || $slots.header" class="panel-toolbar">
        <slot name="header">
          <h2>{{ title }}</h2>
        </slot>
        <span class="spacer" />
        <button
          type="button"
          class="btn-icon"
          aria-label="Close"
          @click="emit('close')"
        >
          <UiIcon :path="mdiClose" />
        </button>
      </div>
      <div class="panel-body">
        <slot />
      </div>
      <div v-if="$slots.actions" class="dialog-actions">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>
