<script setup lang="ts">
// Create or edit an MCU type - fw.type.add/.update, mirroring
// FirmwareUpdaterPanelTypeDialog.vue. `targets[]` (the fw.status projection)
// doesn't carry chipset/katapult_installed/extra_args, so editing fetches the
// same on-demand detail TargetRow's "Show detail" already uses
// (fw.target.get) rather than adding a second wire shape for the same data.
import { computed, ref, watch } from "vue";
import { addType, fetchTargetDetail, updateType } from "../store/agent";
import {
  TYPE_NAME_MAX,
  validateTypeName,
  type Family,
  type TypeDraft,
} from "../api/mcutype";
import UiDialog from "./UiDialog.vue";

const props = defineProps<{
  /** Present when editing an existing type; absent when creating one. */
  typeName?: string | null;
  existingNames: string[];
  families: Family[];
  /** Pre-fills the chipset when creating from a device already scanned. */
  suggestedChipset?: string | null;
  /** A board to adopt once the type exists - the untracked-device entry
   * point. Empty when opened from the toolbar's "New type…". */
  serial?: string | null;
}>();

const emit = defineEmits<{ close: [] }>();

interface FamilyBlock {
  extra_args: string;
  makefile_patches?: { file: string; line: string }[];
}

interface TypeDetail {
  chipset: string;
  firmware: string;
  katapult_installed: boolean;
  katapult?: FamilyBlock;
  artifacts?: Record<string, { has_bin?: boolean } | undefined>;
  [family: string]: unknown;
}

const editing = computed(() => !!props.typeName);
const loading = ref(false);
const detail = ref<TypeDetail | null>(null);
const saving = ref(false);
const warnings = ref<string[]>([]);

const name = ref("");
const chipset = ref("");
const firmware = ref("klipper");
const applicationExtraArgs = ref("");
const katapultExtraArgs = ref("");
const katapultInstalled = ref(true);

// Katapult is what puts firmware on a board, not a board's application - a
// type never picks it as its own firmware.
const applicationFamilies = computed(() =>
  props.families.filter((f) => !f.bootloader),
);

function applicationBlock(): FamilyBlock | undefined {
  if (!detail.value) return undefined;
  return detail.value[detail.value.firmware] as FamilyBlock | undefined;
}

function resetFromDetail(): void {
  name.value = props.typeName ?? "";
  chipset.value = detail.value?.chipset ?? props.suggestedChipset ?? "";
  firmware.value = detail.value?.firmware ?? "klipper";
  applicationExtraArgs.value = applicationBlock()?.extra_args ?? "";
  katapultExtraArgs.value = detail.value?.katapult?.extra_args ?? "";
  katapultInstalled.value = detail.value?.katapult_installed ?? true;
}

async function load(): Promise<void> {
  warnings.value = [];
  if (!editing.value) {
    resetFromDetail();
    return;
  }
  loading.value = true;
  detail.value = (await fetchTargetDetail(
    props.typeName as string,
    "kconfig_make",
  )) as TypeDetail | null;
  loading.value = false;
  resetFromDetail();
}

watch(() => props.typeName, load, { immediate: true });

const nameError = computed(() =>
  editing.value ? null : validateTypeName(name.value, props.existingNames),
);

const hasBinary = computed(() => {
  const family = detail.value?.firmware;
  if (!family || !detail.value?.artifacts) return false;
  return detail.value.artifacts[family]?.has_bin === true;
});

// Staleness compares the source commit and a hash of the .config - neither
// changes when the chipset or the firmware family does, so a binary built
// for the old chip (or the old tree) would keep reporting itself as fresh.
// Say so here, before the fact, rather than let it be flashed silently wrong.
function checkChangeWarnings(): void {
  warnings.value = [];
  if (!editing.value || !hasBinary.value) return;
  if (chipset.value !== detail.value?.chipset) {
    warnings.value.push(
      `The built firmware for '${name.value}' was compiled for ${detail.value?.chipset}. Rebuild before flashing - staleness cannot detect a chipset change on its own.`,
    );
  }
  if (firmware.value !== detail.value?.firmware) {
    warnings.value.push(
      `The built firmware for '${name.value}' came from ${detail.value?.firmware}. Rebuild before flashing - staleness compares a tree against itself and cannot detect the tree being swapped.`,
    );
  }
}

watch([chipset, firmware], checkChangeWarnings);

const firmwareMissing = computed(() => {
  const family = props.families.find((f) => f.name === firmware.value);
  return !!family && !family.present;
});

const canSubmit = computed(() => {
  if (!chipset.value.trim()) return false;
  if (editing.value) return true;
  return nameError.value === null;
});

async function submit(): Promise<void> {
  if (!canSubmit.value) return;
  saving.value = true;
  let ok: boolean;
  if (editing.value) {
    // Only the keys this form shows - the agent leaves anything else alone.
    // The extra-args key is dynamic and keyed on the *saved* application
    // (detail.value.firmware), not the family just picked in this same
    // edit - that family has no answers of its own yet.
    const savedFamily = detail.value?.firmware ?? "klipper";
    ok = await updateType(name.value, {
      chipset: chipset.value.trim(),
      firmware: firmware.value,
      [`${savedFamily}_extra_args`]: applicationExtraArgs.value,
      katapult_extra_args: katapultExtraArgs.value,
      katapult_installed: katapultInstalled.value,
    });
  } else {
    const draft: TypeDraft = {
      name: name.value.trim(),
      chipset: chipset.value.trim(),
      firmware: firmware.value,
      applicationExtraArgs: applicationExtraArgs.value,
      katapultExtraArgs: katapultExtraArgs.value,
      katapultInstalled: katapultInstalled.value,
      serial: props.serial ?? undefined,
    };
    ok = await addType(draft);
  }
  saving.value = false;
  if (ok) emit("close");
}

const patchLines = computed(() => {
  const patches = applicationBlock()?.makefile_patches ?? [];
  return patches.map((p) => `${p.file} -> ${p.line}`);
});
</script>

<template>
  <UiDialog
    :title="editing ? 'Edit type' : 'Create a new type'"
    @close="emit('close')"
  >
    <p v-if="loading">Loading…</p>
    <template v-else>
      <label v-if="!editing">
        Name
        <input v-model="name" :maxlength="TYPE_NAME_MAX" />
      </label>
      <div v-else>
        <div class="text-caption text--disabled">Name</div>
        <div>{{ name }}</div>
        <div class="text-caption text--disabled">
          Renaming isn't supported - the name is also the directory holding this
          type's saved menuconfig answers.
        </div>
      </div>
      <p v-if="nameError" class="alert alert--error">{{ nameError }}</p>

      <label>
        Chipset
        <input v-model="chipset" placeholder="e.g. stm32g0b1xx" />
      </label>

      <label v-if="applicationFamilies.length > 1">
        Firmware
        <select v-model="firmware">
          <option
            v-for="f in applicationFamilies"
            :key="f.name"
            :value="f.name"
          >
            {{ f.present ? f.name : `${f.name} (source not found)` }}
          </option>
        </select>
      </label>
      <p v-if="firmwareMissing" class="alert alert--warning">
        {{ families.find((f) => f.name === firmware)?.source }} hasn't been
        checked out yet - declaring the type now and cloning the source after is
        fine, but nothing will build here until it exists.
      </p>

      <p
        v-for="warning in warnings"
        :key="warning"
        class="alert alert--warning"
      >
        {{ warning }}
      </p>

      <label>
        Klipper extra args
        <input v-model="applicationExtraArgs" />
      </label>
      <label>
        Katapult extra args
        <input v-model="katapultExtraArgs" />
      </label>
      <label class="checkbox-row">
        <input v-model="katapultInstalled" type="checkbox" class="switch" />
        Katapult installed
      </label>

      <div v-if="patchLines.length" class="detail-block">
        <div class="text-caption text--disabled">Makefile patches</div>
        <code v-for="line in patchLines" :key="line" class="text-caption">{{
          line
        }}</code>
      </div>

      <p v-if="serial" class="alert alert--info">
        Will also track {{ serial }} under this type once it's created.
      </p>
    </template>

    <template #actions>
      <button type="button" @click="emit('close')">Cancel</button>
      <button type="button" :disabled="!canSubmit || saving" @click="submit">
        {{ saving ? "Working…" : editing ? "Save" : "Create" }}
      </button>
    </template>
  </UiDialog>
</template>

<style scoped>
label {
  display: block;
  margin-bottom: 10px;
}

label input:not(.switch),
label select {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin-top: 2px;
}

.checkbox-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.checkbox-row input:not(.switch) {
  width: auto;
}

.detail-block {
  margin: 8px 0;
  padding: 6px 8px;
  border-radius: 4px;
  background-color: var(--color-inset);
}

.detail-block code {
  display: block;
}
</style>
