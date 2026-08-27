import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import JobPanel from "./JobPanel.vue";
import { state } from "../store/agent";
import * as store from "../store/agent";
import type { Job } from "../api/jobs";

const runningBuild: Job = {
  id: "job-1",
  kind: "build",
  params: { name: "carto_v4" },
  state: "running",
  created: 0,
  started: 0,
  finished: null,
  duration: 3.2,
  progress: { step: "Compiling", index: 0, total: 1 },
  result: null,
  error: null,
  cancel_requested: false,
  log_next: 2,
  log_dropped: 0,
};

afterEach(() => {
  vi.restoreAllMocks();
  state.job = null;
  state.log = null;
  state.logOmitted = false;
});

describe("JobPanel", () => {
  it("renders nothing without a job", () => {
    const wrapper = mount(JobPanel);
    expect(wrapper.find("section").exists()).toBe(false);
  });

  it("shows 1-based progress from a 0-based index", () => {
    state.job = runningBuild;
    const wrapper = mount(JobPanel);
    expect(wrapper.text()).toContain("Compiling (1/1)");
  });

  it("says a build cancel is immediate", () => {
    state.job = runningBuild;
    const wrapper = mount(JobPanel);
    expect(wrapper.text()).toContain("immediately");
    expect(wrapper.text()).not.toContain("half-written");
  });

  it("says a flash cancel is deferred, and warns about a half-written board", () => {
    state.job = { ...runningBuild, kind: "flash_all" };
    const wrapper = mount(JobPanel);
    expect(wrapper.text()).toContain("half-written");
  });

  it("shows the omitted-lines marker only when the store flagged it", () => {
    state.job = runningBuild;
    state.log = {
      job_id: "job-1",
      lines: [{ i: 1, s: "stdout", t: "hi" }],
    };
    state.logOmitted = true;
    const wrapper = mount(JobPanel);
    expect(wrapper.text()).toContain("dropped from the buffer");
  });

  it("cancels through the store on click", async () => {
    state.job = runningBuild;
    const spy = vi.spyOn(store, "cancelJob").mockResolvedValue(true);
    const wrapper = mount(JobPanel);
    await wrapper.get("button").trigger("click");
    expect(spy).toHaveBeenCalled();
  });

  it("hides the cancel button once cancellation was requested", () => {
    state.job = { ...runningBuild, cancel_requested: true };
    const wrapper = mount(JobPanel);
    expect(wrapper.find("button").exists()).toBe(false);
    expect(wrapper.text()).toContain("Cancelling…");
  });
});
