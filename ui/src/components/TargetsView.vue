<script setup lang="ts">
// targets[] said in one shape, rendered through one row component - this is
// Phase 4's whole point. See docs/decisions.md.
import { computed } from "vue";
import TargetRow from "./TargetRow.vue";
import AddMcuWizard from "./AddMcuWizard.vue";
import UiPanel from "./UiPanel.vue";
import { mdiChip } from "../icons";
import { targetKey, type Target } from "../api/targets";

const props = defineProps<{ targets: Target[] | undefined }>();

const targets = computed(() => props.targets ?? []);
</script>

<template>
  <UiPanel title="Firmware" :icon="mdiChip">
    <template #buttons>
      <!-- The launcher for a brand-new, not-yet-tracked board sits in the
           panel's own toolbar, mirroring where FirmwareUpdaterPanel.vue puts
           it - AddMcuWizard keeps its own open/scan/start state and its own
           visibility check (a type with a DFU/BOOTSEL path exists), this
           just gives it somewhere to render its trigger. -->
      <AddMcuWizard />
    </template>

    <p v-if="targets.length === 0" class="muted">No targets configured yet.</p>
    <!-- No separator element here - TargetRow ends with its own trailing
         divider, same as FirmwareUpdaterPanelTarget.vue's <v-divider>. -->
    <TargetRow
      v-for="target in targets"
      :key="targetKey(target)"
      :target="target"
    />
  </UiPanel>
</template>
