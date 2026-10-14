import { beforeEach, describe, expect, it } from "vitest";
import { translation } from "../src/cad/path";
import { RectangleBoundaryConflictError, followedDomainBounds, partsBounds, partsCrossAxis, setComputationalDomainMode } from "../src/model/computationalDomain";
import { useDocument } from "../src/model/documentStore";
import { transformItems } from "../src/model/editOps";
import { ProjectFormatError, axisEdges, domainBounds, newProject, normalizeProject, serializeProject } from "../src/model/project";
import { addCircleRegionAt, deleteRegion, moveRegion, setCircleRadius } from "../src/model/regionOps";

const doc = () => useDocument.getState();
beforeEach(() => doc().replace(newProject("xy"), null));

describe("parts-first computational domain", () => {
  it("starts automatic with a sensible empty domain, and migrates old files without changing physics", () => {
    expect(doc().project.geometry.computational_domain).toEqual({ mode: "auto", margin: 0.005 });
    expect(domainBounds(doc().project)).toEqual({ x0: 0, y0: 0, x1: 0.1, y1: 0.05 });
    const old = newProject("xy");
    delete old.geometry.computational_domain;
    old.geometry.regions.push({ id: "outside", type: "dielectric", shape: { kind: "circle", center: [0.3, 0.2], radius: 0.04 } });
    const loaded = normalizeProject(JSON.parse(serializeProject(old))).project;
    expect(loaded.geometry.computational_domain?.mode).toBe("manual");
    expect(loaded.geometry.domain).toEqual(old.geometry.domain);
    expect(loaded.geometry.boundaries).toEqual(old.geometry.boundaries);
    doc().replace(loaded, null);
    doc().update("move", (d) => moveRegion(d, "outside", 1, 1));
    expect(doc().project.geometry.domain).toEqual(old.geometry.domain);
  });

  it("follows parts outside the old domain, with add/move/transform/resize/delete and undo/redo in one step", () => {
    let id = "";
    doc().update("add", (d) => { id = addCircleRegionAt(d, [0.25, -0.08], 0.02)!; });
    expect(domainBounds(doc().project)).toEqual({ x0: 0.225, y0: -0.105, x1: 0.275, y1: -0.055 });
    expect(doc().past).toHaveLength(1);
    const added = doc().project;
    doc().update("move", (d) => moveRegion(d, id, 0.3, 0.5));
    expect(domainBounds(doc().project)).toEqual({ x0: 0.525, y0: 0.395, x1: 0.575, y1: 0.445 });
    doc().undo();
    expect(doc().project).toEqual(added);
    doc().redo();
    doc().update("transform", (d) => { transformItems(d, [{ kind: "region", id }], translation(-0.2, 0.1), false); });
    doc().update("radius", (d) => setCircleRadius(d, id, 0.06));
    expect(domainBounds(doc().project)).toEqual({ x0: 0.285, y0: 0.455, x1: 0.415, y1: 0.585 });
    const resized = doc().project;
    doc().update("delete", (d) => deleteRegion(d, id));
    expect(domainBounds(doc().project)).toEqual({ x0: 0, y0: 0, x1: 0.1, y1: 0.05 });
    doc().undo();
    expect(doc().project).toEqual(resized);
    expect(doc().project.geometry.domain.edge_ids).toEqual(["e1", "e2", "e3", "e4"]);
    expect(doc().project.geometry.boundaries.map((b) => b.edges)).toEqual([[3], [1]]);
  });

  it("includes exact curved extents and every physical part, while leaving construction sketches out", () => {
    const p = newProject("xy");
    p.geometry.regions = [{ id: "arc", type: "dielectric", polygon: [[-0.02, 0], [0.02, 0]], bulges: [1, 1] }];
    p.cad = { sketch: [{ kind: "circle", id: "s1", center: [50, 50], r: 2 }] };
    expect(partsBounds(p)).toEqual({ x0: -0.02, y0: -0.02, x1: 0.02, y1: 0.02 });
    expect(followedDomainBounds(p)).toEqual({ x0: -0.025, y0: -0.025, x1: 0.025, y1: 0.025 });
  });

  it.each(["rz", "rz_x0"] as const)("clips negative radial extents in %s without moving the part", (coord) => {
    doc().update("axis part", (d) => {
      d.coord = coord;
      addCircleRegionAt(d, [0, 0], 0.02);
      d.geometry.boundaries.push({ type: "dirichlet", edges: [0, 3], voltage: 12 });
    });
    const p = doc().project;
    expect(partsCrossAxis(p)).toBe(true);
    expect(p.geometry.regions[0].shape?.center).toEqual([0, 0]);
    expect(coord === "rz" ? domainBounds(p).y0 : domainBounds(p).x0).toBe(0);
    expect(axisEdges(p)).toEqual([coord === "rz" ? 0 : 3]);
    expect(p.geometry.boundaries.every((bc) => !bc.edges.some((e) => axisEdges(p).includes(e)))).toBe(true);
  });

  it("maps shifted and clockwise legacy rectangle side IDs and every edge reference to canonical order", () => {
    const p = newProject("xy");
    p.geometry.computational_domain!.mode = "manual";
    // CW: top, right, bottom, left, starting at upper left.
    p.geometry.domain.polygon = [[0, 0.05], [0.1, 0.05], [0.1, 0], [0, 0]];
    p.geometry.domain.edge_ids = ["top", "right", "bottom", "left"];
    p.geometry.boundaries = [{ type: "dirichlet", edges: [0], voltage: 1 }, { type: "dirichlet", edges: [2], voltage: 2 }];
    p.pic = { reflect_edges: [1, 3] };
    doc().replace(p, null);
    doc().update("auto", (d) => setComputationalDomainMode(d, "auto"));
    expect(doc().project.geometry.domain.edge_ids).toEqual(["bottom", "right", "top", "left"]);
    expect(doc().project.geometry.boundaries.map((bc) => bc.edges)).toEqual([[2], [0]]);
    expect(doc().project.pic?.reflect_edges).toEqual([1, 3]);
    doc().undo();
    expect(doc().project).toEqual(p);
  });

  it("keeps margin and part parameter bindings, removes domain bindings, and round-trips automatic tracking", () => {
    const p = newProject("xy");
    p.geometry.computational_domain!.mode = "manual";
    p.geometry.regions = [{ id: "r1", type: "conductor", shape: { kind: "circle", center: [0.25, 0.1], radius: 0.02 } }];
    p.params = { vars: [{ name: "gap", expr: "5 mm" }], bindings: [
      { path: ["geometry", "domain", "polygon", 1, 0], expr: "gap * 20" },
      { path: ["geometry", "computational_domain", "margin"], expr: "gap" },
      { path: ["geometry", "regions", { id: "r1" }, "shape", "radius"], expr: "gap * 4" },
    ] };
    doc().replace(p, null);
    doc().update("auto", (d) => setComputationalDomainMode(d, "auto"));
    doc().update("parameter", (d) => { (d.params as { vars: { expr: string }[] }).vars[0].expr = "10 mm"; });
    expect(doc().project.geometry.computational_domain?.margin).toBe(0.01);
    expect(doc().project.geometry.regions[0].shape?.radius).toBe(0.04);
    expect(domainBounds(doc().project)).toEqual({ x0: 0.2, y0: 0.05, x1: 0.3, y1: 0.15 });
    expect((doc().project.params as { bindings: unknown[] }).bindings).toHaveLength(2);
    const saved = normalizeProject(JSON.parse(serializeProject(doc().project))).project;
    expect(saved).toEqual(doc().project);
    doc().update("manual", (d) => setComputationalDomainMode(d, "manual"));
    const fixed = doc().project.geometry.domain;
    doc().update("move", (d) => moveRegion(d, "r1", 0.5, 0));
    expect(doc().project.geometry.domain).toBe(fixed);
  });

  it("rejects merging subdivided sides with different physical conditions, preserving the whole manual document", () => {
    const p = newProject("xy");
    p.geometry.computational_domain!.mode = "manual";
    p.geometry.domain.polygon = [[0, 0], [0.1, 0], [0.1, 0.025], [0.1, 0.05], [0, 0.05]];
    p.geometry.domain.edge_ids = ["bottom", "right-low", "right-high", "top", "left"];
    p.geometry.boundaries = [{ type: "dirichlet", edges: [1], voltage: 0 }, { type: "dirichlet", edges: [2], voltage: 100 }];
    doc().replace(p, null);
    expect(() => doc().update("auto", (d) => setComputationalDomainMode(d, "auto"))).toThrow(RectangleBoundaryConflictError);
    expect(doc().project).toBe(p);
    expect(doc().past).toHaveLength(0);
    const badAuto = structuredClone(p);
    badAuto.geometry.computational_domain!.mode = "auto";
    expect(() => normalizeProject(badAuto)).toThrow(ProjectFormatError);
    const natural = structuredClone(p);
    natural.geometry.boundaries.splice(0, 1); // 自然境界とDirichletも同じ側へまとめられない。
    doc().replace(natural, null);
    expect(() => doc().update("auto", (d) => setComputationalDomainMode(d, "auto"))).toThrow(RectangleBoundaryConflictError);
  });

  it("rejects merging a partly reflective side instead of changing particle boundary physics", () => {
    const p = newProject("xy");
    p.geometry.computational_domain!.mode = "manual";
    p.geometry.domain.polygon = [[0, 0], [0.1, 0], [0.1, 0.025], [0.1, 0.05], [0, 0.05]];
    p.geometry.domain.edge_ids = ["bottom", "right-low", "right-high", "top", "left"];
    p.geometry.boundaries = [];
    p.pic = { reflect_edges: [2] };
    doc().replace(p, null);
    expect(() => doc().update("auto", (d) => setComputationalDomainMode(d, "auto"))).toThrow(RectangleBoundaryConflictError);
    expect(doc().project.pic?.reflect_edges).toEqual([2]);
    expect(doc().project.geometry.computational_domain?.mode).toBe("manual");
  });

  it("merges matching side conditions and remaps matching boundary parameter addresses", () => {
    const p = newProject("xy");
    p.geometry.computational_domain!.mode = "manual";
    p.geometry.domain.polygon = [[0, 0], [0.1, 0], [0.1, 0.025], [0.1, 0.05], [0, 0.05]];
    p.geometry.domain.edge_ids = ["bottom", "right-low", "right-high", "top", "left"];
    p.geometry.boundaries = [{ type: "dirichlet", edges: [1], voltage: 100 }, { type: "dirichlet", edges: [2], voltage: 100 }];
    p.params = { vars: [{ name: "bias", expr: "100" }], bindings: [
      { path: ["geometry", "boundaries", { edge: "right-low" }, "voltage"], expr: "bias" },
      { path: ["geometry", "boundaries", { edge: "right-high" }, "voltage"], expr: "bias" },
    ] };
    doc().replace(p, null);
    doc().update("auto", (d) => setComputationalDomainMode(d, "auto"));
    expect(doc().project.geometry.boundaries).toEqual([{ type: "dirichlet", edges: [1], voltage: 100 }]);
    expect((doc().project.params as { bindings: unknown[] }).bindings).toHaveLength(1);
    doc().update("bias", (d) => { (d.params as { vars: { expr: string }[] }).vars[0].expr = "200"; });
    expect(doc().project.geometry.boundaries[0].voltage).toBe(200);
    expect((doc().project.params as { bindings: unknown[] }).bindings).toHaveLength(1);
  });
});
