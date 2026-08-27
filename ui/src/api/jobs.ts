// Wire types for the job/log machinery - docs/agent-api.md's "Jobs" and
// "The log, and its sequence numbers" sections.

export type JobKind =
  "build" | "build_all" | "flash" | "flash_all" | "update_all";

export type JobState =
  "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface JobProgress {
  step: string;
  /** 0-based - render `index + 1` of `total`. */
  index: number;
  total: number;
}

export interface JobError {
  code: string;
  message: string;
  data?: unknown;
}

export interface Job {
  id: string;
  kind: JobKind;
  params: Record<string, unknown>;
  state: JobState;
  created: number;
  started: number | null;
  finished: number | null;
  duration: number | null;
  progress: JobProgress | null;
  result: unknown;
  error: JobError | null;
  cancel_requested: boolean;
  log_next: number;
  log_dropped: number;
}

export interface LogLine {
  i: number;
  s: "stdout" | "info" | "warn" | "error" | "cmd";
  t: string;
}

/** `fw.job.cancel`'s kinds that only take effect between devices, never mid-
 * write - docs/agent-api.md's "Cancellation is not uniform" table. Kept in
 * exactly one place so nothing has to re-derive it, and so
 * tests/test_ui_contract.py's fw.* scan sees it. */
const DEFERRED_CANCEL_KINDS: ReadonlySet<JobKind> = new Set([
  "flash",
  "flash_all",
  "update_all",
]);

/** What to tell the user a cancel request will actually do, from the job's
 * own `kind` - this has to survive a page reload, where `fw.job.cancel`'s
 * returned `immediate` is long gone but `job.kind` is still on the job. */
export function cancelIsImmediate(kind: JobKind): boolean {
  return !DEFERRED_CANCEL_KINDS.has(kind);
}
