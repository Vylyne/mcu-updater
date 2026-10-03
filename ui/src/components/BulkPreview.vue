<script setup lang="ts">
// The body of every confirmation that writes firmware: the fleet-wide one in
// BulkDialog.vue and a row's or a device's in ActionButton.vue. One component
// because the two used to be laid out separately and drifted - the type's
// flash said "Confirm" over a bare list while the fleet's said what it would
// build, what it would flash and what stopping Klipper costs. Presentational
// only: each caller still owns the call it makes and its own buttons.
import type { BulkScope } from "../api/bulk";

export interface PreviewRow {
  key: string;
  name: string;
  detail: string | null;
}

withDefaults(
  defineProps<{
    body: string;
    /** The "Everything, not just what looks stale" switch, bound through
     * v-model:scope. Off for a single device, which has no scope. */
    offersScope?: boolean;
    /** null hides the section entirely - a flash has no build list. */
    builds?: PreviewRow[] | null;
    flashes?: PreviewRow[] | null;
    hasWork: boolean;
    /** "The agent re-decides" - true of a selection, not of one named
     * device the agent writes exactly as asked. */
    caveat?: boolean;
    /** An update's flash list can only grow once its builds land. */
    floorNote?: boolean;
    writesToBoards?: boolean;
    busyMessage?: string | null;
  }>(),
  {
    offersScope: false,
    builds: null,
    flashes: null,
    caveat: true,
    floorNote: false,
    writesToBoards: true,
    busyMessage: null,
  },
);

const scope = defineModel<BulkScope>("scope", { default: "stale" });
</script>

<template>
  <p>{{ body }}</p>

  <template v-if="offersScope">
    <label class="scope-toggle">
      <input
        v-model="scope"
        type="checkbox"
        true-value="all"
        false-value="stale"
        class="switch"
      />
      Everything, not just what looks stale
    </label>
    <p class="text-caption text--secondary">
      {{
        scope === "all"
          ? "Ignores the recorded provenance - use this when you edited a source the provenance can't see."
          : "Only what the recorded provenance says needs doing."
      }}
    </p>
  </template>

  <template v-if="hasWork">
    <template v-if="builds !== null">
      <p class="text-caption text--secondary">Will build:</p>
      <div v-if="builds.length" class="detail-block">
        <div v-for="row in builds" :key="row.key">
          <strong>{{ row.name }}</strong>
          <span v-if="row.detail" class="text-caption text--secondary">{{
            row.detail
          }}</span>
        </div>
      </div>
      <p v-else class="text--disabled text-caption">Nothing to build.</p>
    </template>

    <template v-if="flashes !== null">
      <p class="text-caption text--secondary">Will flash:</p>
      <div v-if="flashes.length" class="detail-block">
        <div v-for="row in flashes" :key="row.key">
          <strong>{{ row.name }}</strong>
          <span v-if="row.detail" class="text-caption text--secondary">{{
            row.detail
          }}</span>
        </div>
      </div>
      <p v-else class="text--disabled text-caption">Nothing to flash.</p>
    </template>
  </template>
  <p v-else class="alert alert--info">Nothing for this to do right now.</p>

  <p v-if="caveat" class="text-caption text--disabled">
    This is a preview only - the agent re-decides what to touch when the call
    actually arrives.
    <template v-if="floorNote">
      The flash list above is a floor, not a forecast: a build can only add
      boards to it.
    </template>
  </p>

  <p v-if="writesToBoards" class="alert alert--warning">
    This stops Klipper and writes to hardware. Do not interrupt it once started.
  </p>
  <p v-if="busyMessage" class="alert alert--error">
    {{ busyMessage }}
  </p>
</template>

<style scoped>
.scope-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 2px;
}

.detail-block {
  margin: 2px 0 10px;
  padding: 6px 8px;
  border-radius: 4px;
  background-color: var(--color-inset);
  max-height: 200px;
  overflow-y: auto;
}

.detail-block > div + div {
  margin-top: 4px;
}

.detail-block strong {
  margin-right: 6px;
}
</style>
