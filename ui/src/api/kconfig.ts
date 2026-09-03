// Wire types for the fw.kconfig.* session - hand-mirrored from
// src/mcu_updater/providers/kconfig.py's KconfigSession.menu()/help()/
// search(), the same way targets.ts mirrors fw.status.

export type KconfigNodeKind =
  | "menu"
  | "comment"
  | "choice"
  | "bool"
  | "tristate"
  | "string"
  | "int"
  | "hex"
  | "unknown";

export interface KconfigNode {
  id: string;
  kind: KconfigNodeKind;
  name: string | null;
  prompt: string;
  depth: number;
  value: string | null;
  visible: boolean;
  /** For a choice these are option *names*; for a bool/tristate they are
   * y/n/m. */
  assignable: string[];
  /** Choices only: the same options with their Kconfig prompts. */
  options: { value: string; label: string }[] | null;
  value_label: string | null;
  /** False means kconfiglib will not accept a change - another symbol's
   * `select` holds it, or its dependencies are unmet. Not the same as a
   * non-empty `assignable`. */
  editable: boolean;
  range: { min: string; max: string } | null;
  has_help: boolean;
  is_menuconfig: boolean;
  enterable: boolean;
}

/** What `fw.kconfig.open`/`.enter`/`.up`/`.set`/`.reset` all return: the
 * current screen. */
export interface KconfigMenu {
  session: string;
  revision: number;
  type: string;
  fw: string;
  dirty: boolean;
  breadcrumb: { id: string; prompt: string }[];
  nodes: KconfigNode[];
  /** CONFIG_ names the agent pre-set from this type's own recorded chipset -
   * only ever non-empty on the `open` that produced them, and only when
   * there was no saved config yet to seed over. Absent (not just empty) on
   * every other call. */
  seeded?: string[];
}

export interface KconfigSearchResult {
  query: string;
  nodes: KconfigNode[];
  truncated: boolean;
}

export interface KconfigHelp {
  id: string;
  prompt: string;
  help: string;
}

/** `search`/`help` are never part of the agent's own menu payload - they are
 * client-side additions layered onto the last menu received, the same way
 * the fork's FwKconfigState does it. */
export interface KconfigState extends KconfigMenu {
  search: KconfigSearchResult | null;
  help: KconfigHelp | null;
}
