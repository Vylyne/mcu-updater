import { onBeforeUnmount, onMounted, type Ref } from "vue";

/** Closes a dropdown menu on an outside click - dialogs deliberately never
 * get this, only menus. A `mousedown` listener (not `click`) so the menu is
 * already closed by the time the outside element's own click runs, the same
 * way a native OS menu behaves. */
export function useClickOutsideToClose(
  container: Ref<HTMLElement | null>,
  open: Ref<boolean>,
): void {
  function onMouseDown(event: MouseEvent): void {
    if (!open.value) return;
    if (container.value?.contains(event.target as Node)) return;
    open.value = false;
  }

  onMounted(() => document.addEventListener("mousedown", onMouseDown));
  onBeforeUnmount(() => document.removeEventListener("mousedown", onMouseDown));
}
