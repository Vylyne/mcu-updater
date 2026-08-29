<script setup lang="ts">
// targets[] said in one shape, rendered through one row component - this is
// Phase 4's whole point. See docs/decisions.md. Phase 10 adds the fleet-wide
// toolbar (build/flash/update all, refresh, new type) FirmwareUpdaterPanel.vue
// carries and this UI didn't yet.
import { computed, nextTick, ref, watch } from "vue";
import { flipMenuIfOffscreen, useClickOutsideToClose } from "../clickOutside";
import TargetRow from "./TargetRow.vue";
import AddMcuWizard from "./AddMcuWizard.vue";
import UiPanel from "./UiPanel.vue";
import UiIcon from "./UiIcon.vue";
import SummaryChips from "./SummaryChips.vue";
import BulkDialog from "./BulkDialog.vue";
import TypeDialog from "./TypeDialog.vue";
import {
  mdiChip,
  mdiDotsVertical,
  mdiFlash,
  mdiHammerWrench,
  mdiPlusCircleOutline,
  mdiRefresh,
  mdiTrayArrowUp,
  mdiUpdate,
} from "../icons";
import { targetKey, type Target } from "../api/targets";
import type { Family } from "../api/mcutype";
import type { BulkOperation } from "../api/bulk";
import { hasCapability, isBusy, refresh, state } from "../store/agent";

const props = defineProps<{ targets: Target[] | undefined }>();

const targets = computed(() => props.targets ?? []);
const families = computed(
  () => (state.status?.firmware_families as Family[] | undefined) ?? [],
);
const existingTypeNames = computed(() =>
  targets.value.filter((t) => t.provider === "kconfig_make").map((t) => t.name),
);

const needsFlashCount = computed(() =>
  targets.value.reduce(
    (total, t) =>
      total + t.devices.filter((d) => d.needs_flash === true).length,
    0,
  ),
);

// The panel hides controls the agent hasn't advertised, so a newer panel
// against an older (or read-only, or flashing-disabled) agent never offers a
// button that would just return -32601.
const canBuildAll = computed(() => hasCapability("fw.build_all"));
const canFlashAll = computed(() => hasCapability("fw.flash_all"));
const canUpdateAll = computed(() => hasCapability("fw.update_all"));
const canManageTypes = computed(
  () =>
    hasCapability("fw.type.add") &&
    hasCapability("fw.type.update") &&
    hasCapability("fw.type.remove"),
);
const canAddMcu = computed(() => hasCapability("fw.add_mcu.start"));
const hasMenu = computed(
  () => canUpdateAll.value || canManageTypes.value || canAddMcu.value,
);

const flashAllIcon = computed(() =>
  needsFlashCount.value ? mdiTrayArrowUp : mdiFlash,
);

const menuOpen = ref(false);
const menuRef = ref<HTMLElement | null>(null);
useClickOutsideToClose(menuRef, menuOpen);
watch(menuOpen, (open) => {
  if (open) void nextTick(() => flipMenuIfOffscreen(menuRef.value));
});
const bulkOperation = ref<BulkOperation | null>(null);
const typeDialogOpen = ref(false);
const refreshing = ref(false);

function openBulk(operation: BulkOperation): void {
  bulkOperation.value = operation;
  menuOpen.value = false;
}

function openTypeDialog(): void {
  typeDialogOpen.value = true;
  menuOpen.value = false;
}

async function onRefresh(): Promise<void> {
  refreshing.value = true;
  await refresh();
  refreshing.value = false;
}
</script>

<template>
  <UiPanel title="Firmware" :icon="mdiChip">
    <template #buttons>
      <span v-if="hasMenu" ref="menuRef" class="target-menu">
        <button
          type="button"
          class="btn-icon"
          aria-label="More actions"
          :disabled="isBusy()"
          @click="menuOpen = !menuOpen"
        >
          <UiIcon :path="mdiDotsVertical" size="small" />
        </button>
        <div v-if="menuOpen" class="menu-list">
          <button
            v-if="canUpdateAll"
            type="button"
            class="menu-item"
            @click="openBulk('update_all')"
          >
            <UiIcon :path="mdiUpdate" size="x-small" />
            Update everything…
          </button>
          <hr
            v-if="canManageTypes && (canUpdateAll || canAddMcu)"
            class="divider"
          />
          <button
            v-if="canManageTypes"
            type="button"
            class="menu-item"
            @click="openTypeDialog"
          >
            <UiIcon :path="mdiPlusCircleOutline" size="x-small" />
            New type…
          </button>
          <AddMcuWizard
            v-if="canAddMcu"
            variant="menu"
            @click="menuOpen = false"
          />
        </div>
      </span>

      <button
        v-if="canBuildAll"
        type="button"
        class="btn-icon btn-icon--primary"
        title="Build everything that needs it"
        :disabled="isBusy()"
        @click="openBulk('build_all')"
      >
        <UiIcon :path="mdiHammerWrench" size="small" />
      </button>

      <button
        v-if="canFlashAll"
        type="button"
        class="btn-icon"
        :class="{ 'btn-icon--primary': needsFlashCount > 0 }"
        title="Flash everything that needs it"
        :disabled="isBusy()"
        @click="openBulk('flash_all')"
      >
        <UiIcon :path="flashAllIcon" size="small" />
      </button>

      <button
        type="button"
        class="btn-icon btn-icon--primary"
        title="Refresh"
        :disabled="refreshing"
        @click="onRefresh"
      >
        <UiIcon :path="mdiRefresh" size="small" />
      </button>
    </template>

    <SummaryChips v-if="targets.length" :targets="targets" />

    <p v-if="targets.length === 0" class="muted">No targets configured yet.</p>
    <!-- No separator element here - TargetRow ends with its own trailing
         divider, same as FirmwareUpdaterPanelTarget.vue's <v-divider>. -->
    <TargetRow
      v-for="target in targets"
      :key="targetKey(target)"
      :target="target"
    />

    <BulkDialog
      v-if="bulkOperation"
      :operation="bulkOperation"
      :targets="targets"
      @close="bulkOperation = null"
    />

    <TypeDialog
      v-if="typeDialogOpen"
      :existing-names="existingTypeNames"
      :families="families"
      @close="typeDialogOpen = false"
    />
  </UiPanel>
</template>

<style scoped>
.target-menu {
  position: relative;
}
</style>
