import { describe, expect, it } from "vitest";
import {
  validateTypeName,
  TYPE_NAME_MAX,
  parseExtraRepos,
  formatExtraRepos,
  parseMakefilePatches,
  formatMakefilePatches,
} from "./mcutype";

describe("validateTypeName", () => {
  it("accepts a normal name", () => {
    expect(validateTypeName("bttebb36", [])).toBeNull();
  });

  it("rejects an empty name", () => {
    expect(validateTypeName("   ", [])).not.toBeNull();
  });

  it("rejects a name with disallowed characters", () => {
    expect(validateTypeName("a/b", [])).not.toBeNull();
    expect(validateTypeName("a b", [])).not.toBeNull();
  });

  it("rejects a name already taken", () => {
    expect(validateTypeName("bttebb36", ["bttebb36"])).not.toBeNull();
  });

  it("rejects a name over the length limit", () => {
    expect(validateTypeName("a".repeat(TYPE_NAME_MAX + 1), [])).not.toBeNull();
  });

  it("accepts a name at exactly the length limit", () => {
    expect(validateTypeName("a".repeat(TYPE_NAME_MAX), [])).toBeNull();
  });
});

describe("parseExtraRepos / formatExtraRepos", () => {
  it("splits one path per line and trims whitespace", () => {
    expect(
      parseExtraRepos("  /home/pi/buffer_manager  \n/home/pi/other\n"),
    ).toEqual(["/home/pi/buffer_manager", "/home/pi/other"]);
  });

  it("drops blank lines", () => {
    expect(parseExtraRepos("/a\n\n  \n/b")).toEqual(["/a", "/b"]);
  });

  it("returns an empty list for empty input", () => {
    expect(parseExtraRepos("")).toEqual([]);
  });

  it("round-trips through format", () => {
    const repos = ["/a", "/b"];
    expect(parseExtraRepos(formatExtraRepos(repos))).toEqual(repos);
  });
});

describe("parseMakefilePatches / formatMakefilePatches", () => {
  it("splits file -> line and trims both sides", () => {
    expect(parseMakefilePatches("src/Makefile -> src-y += buffer.c")).toEqual([
      { file: "src/Makefile", line: "src-y += buffer.c" },
    ]);
  });

  it("drops blank lines", () => {
    expect(parseMakefilePatches("a -> b\n\nc -> d")).toEqual([
      { file: "a", line: "b" },
      { file: "c", line: "d" },
    ]);
  });

  it("keeps a line with no separator as an incomplete patch rather than dropping it", () => {
    expect(parseMakefilePatches("src/Makefile")).toEqual([
      { file: "src/Makefile", line: "" },
    ]);
  });

  it("round-trips through format", () => {
    const patches = [{ file: "a", line: "b" }];
    expect(parseMakefilePatches(formatMakefilePatches(patches))).toEqual(
      patches,
    );
  });
});
