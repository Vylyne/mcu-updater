// Wire types and validation for fw.type.add/.update/.remove -
// docs/agent-api.md's `Family` section and registry.py's type_add/
// type_update/type_remove. Kept as pure functions/types, no store import, the
// same pattern api/bulk.ts and api/jobs.ts's cancelIsImmediate follow.

/** One entry of fw.status's `firmware_families` - a picker's source list for
 * "which firmware does this board run". `present`/`configurable` are
 * separate answers: a declared family whose tree is not cloned yet is a real,
 * offerable state, not an error. `bootloader` marks katapult-shaped families,
 * which a type never picks as its application. */
export interface Family {
  name: string;
  source: string;
  artifact: string;
  builder: string;
  bootloader: boolean;
  present: boolean;
  configurable: boolean;
  builtin: boolean;
}

/** What TypeDialog collects before calling addType/updateType. */
export interface TypeDraft {
  name: string;
  chipset: string;
  firmware: string;
  applicationExtraArgs: string;
  katapultExtraArgs: string;
  katapultInstalled: boolean;
  /** A board to adopt once the type exists - the untracked-device entry
   * point. Empty when opened from the toolbar's "New type…". */
  serial?: string;
}

/** Mirrors config.py's TYPE_NAME_RE/TYPE_NAME_MAX exactly - a whitelist, not
 * a blacklist, because the name becomes both a config section and a
 * directory. The agent stays the authority; this only spares a round trip
 * and says why before the fact, the same reasoning the fork's own
 * FirmwareUpdaterPanelTypeDialog.vue gives for its identical regex. */
export const TYPE_NAME_RE = /^[A-Za-z0-9._-]+$/;
export const TYPE_NAME_MAX = 64;

/** Returns an error message, or null when the name is fine to submit. */
export function validateTypeName(
  name: string,
  existingNames: string[],
): string | null {
  const trimmed = name.trim();
  if (!trimmed) return "A type name is required.";
  if (trimmed.length > TYPE_NAME_MAX) {
    return `Type names are limited to ${TYPE_NAME_MAX} characters.`;
  }
  if (!TYPE_NAME_RE.test(trimmed)) {
    return "Only letters, digits, '.', '_' and '-' are allowed.";
  }
  if (existingNames.includes(trimmed)) {
    return "A type with this name already exists.";
  }
  return null;
}
