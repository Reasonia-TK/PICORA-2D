import { produce } from "immer";
import { beforeEach, describe, expect, it } from "vitest";
import { shapeArea } from "../src/cad/boolean";
import { useDocument } from "../src/model/documentStore";
import { addLayer, setActiveLayer, updateLayer } from "../src/model/layers";
import { newProject, normalizeProject, regionArea, regionHoles, regionPath, serializeProject, type Point } from "../src/model/project";
import { useSelection } from "../src/model/selection";
import { addSketch, replaceSketch, sketchOf, type NewSketch } from "../src/model/sketch";

const line = (a: Point, b: Point, construction = false): NewSketch => ({ kind: "line", a, b, construction });
const square = (x = 0.02, y = 0.01, size = 0.02): NewSketch[] => [
  line([x, y], [x + size, y]), line([x + size, y], [x + size, y + size]),
  line([x + size, y + size], [x, y + size]), line([x, y + size], [x, y]),
];
const doc = () => useDocument.getState();
const draw = (shapes: NewSketch[]) => doc().update("draw", (d) => { shapes.forEach((e) => addSketch(d, e)); });

beforeEach(() => {
  doc().replace(newProject(), null);
  useSelection.getState().pick([]);
});

describe("closed sketches become domains", () => {
  it("promotes separately drawn lines regardless of order/direction only when the loop closes", () => {
    const edges = square();
    draw([edges[2], edges[0], { ...edges[1], a: [0.04, 0.03], b: [0.04, 0.01] } as NewSketch]);
    expect(doc().project.geometry.regions).toHaveLength(0);
    expect(sketchOf(doc().project)).toHaveLength(3);
    draw([edges[3]]);
    const p = doc().project;
    expect(p.geometry.regions).toHaveLength(1);
    expect(regionArea(p.geometry.regions[0])).toBeCloseTo(0.0004, 12);
    expect(sketchOf(p)).toHaveLength(0);
    expect(p.geometry.domain.polygon).toEqual([[0.015, 0.005], [0.045, 0.005], [0.045, 0.035], [0.015, 0.035]]);
    expect(doc().sketchPromotions[0].sketchIds).toHaveLength(4);
  });

  it("joins numerical endpoint noise without closing a visible gap", () => {
    const edges = square();
    edges[3] = line([0.02, 0.03], [0.02, 0.010001]);
    draw(edges);
    expect(doc().project.geometry.regions).toHaveLength(0);
    doc().update("connect", (d) => replaceSketch(d, "s4", line([0.02, 0.03], [0.02, 0.010000000001])));
    expect(doc().project.geometry.regions).toHaveLength(1);
    expect(sketchOf(doc().project)).toHaveLength(0);
  });

  it("recognizes an open polyline whose last endpoint meets the first", () => {
    draw([{ kind: "polyline", points: [[0.02, 0.01], [0.04, 0.01], [0.03, 0.03], [0.02, 0.01]], closed: false }]);
    expect(doc().project.geometry.regions[0].polygon).toHaveLength(3);
    expect(regionArea(doc().project.geometry.regions[0])).toBeCloseTo(0.0002, 12);
  });

  it("keeps exact curved boundaries made of two opposite-directed arcs", () => {
    draw([{ kind: "arc", a: [0.04, 0.02], b: [0.02, 0.02], bulge: 1 }]);
    expect(doc().project.geometry.regions).toHaveLength(0);
    draw([{ kind: "arc", a: [0.04, 0.02], b: [0.02, 0.02], bulge: -1 }]);
    const r = doc().project.geometry.regions[0];
    expect(r.bulges).toHaveLength(2);
    expect(regionArea(r)).toBeCloseTo(Math.PI * 0.01 ** 2, 12);
  });

  it("promotes a circle with its radius-editable shape intact", () => {
    draw([{ kind: "circle", center: [0.03, 0.02], r: 0.005 }]);
    expect(doc().project.geometry.regions[0].shape).toEqual({ kind: "circle", center: [0.03, 0.02], radius: 0.005 });
    expect(sketchOf(doc().project)).toHaveLength(0);
  });

  it("retains open and zero-area sketches", () => {
    draw([line([0.02, 0.01], [0.04, 0.01]), line([0.04, 0.01], [0.03, 0.01]), line([0.03, 0.01], [0.02, 0.01])]);
    expect(doc().project.geometry.regions).toHaveLength(0);
    expect(sketchOf(doc().project)).toHaveLength(3);
  });

  it("retains a dangling branch and unrelated open line", () => {
    draw([...square(), line([0.02, 0.01], [0.01, 0.005]), line([0.08, 0.01], [0.08, 0.04])]);
    expect(doc().project.geometry.regions).toHaveLength(1);
    expect(sketchOf(doc().project)).toMatchObject([
      { kind: "line", a: [0.02, 0.01], b: [0.01, 0.005] }, { kind: "line", a: [0.08, 0.01], b: [0.08, 0.04] },
    ]);
  });

  it("consumes only the portions enclosing a cell and retains both protruding ends", () => {
    draw([line([0.01, 0.01], [0.05, 0.01]), line([0.01, 0.03], [0.05, 0.03]),
      line([0.02, 0], [0.02, 0.04]), line([0.04, 0], [0.04, 0.04])]);
    expect(doc().project.geometry.regions).toHaveLength(1);
    expect(regionArea(doc().project.geometry.regions[0])).toBeCloseTo(0.0004, 12);
    const remaining = sketchOf(doc().project);
    expect(remaining).toHaveLength(8);
    expect(new Set(remaining.map((e) => e.id)).size).toBe(8);
    expect(remaining.every((e) => e.kind === "line")).toBe(true);
  });

  it("deduplicates coincident boundaries and creates adjacent faces once", () => {
    draw([...square(), line([0.04, 0.01], [0.02, 0.01]), line([0.03, 0.01], [0.03, 0.03])]);
    expect(doc().project.geometry.regions).toHaveLength(2);
    doc().project.geometry.regions.forEach((r) => expect(regionArea(r)).toBeCloseTo(0.0002, 12));
    expect(sketchOf(doc().project)).toHaveLength(0);
    doc().update("unrelated", (d) => { d.mesh.size = 0.0004; });
    expect(doc().project.geometry.regions).toHaveLength(2);
  });

  it("creates separate, non-overlapping faces with holes for nested outlines", () => {
    draw([...square(), { kind: "circle", center: [0.03, 0.02], r: 0.004 }]);
    const regions = doc().project.geometry.regions;
    expect(regions).toHaveLength(2);
    expect(regions.filter((r) => regionHoles(r).length)).toHaveLength(1);
    expect(regions.reduce((area, r) => area + shapeArea({ outer: regionPath(r), holes: regionHoles(r) }), 0)).toBeCloseTo(0.0004, 12);
    expect(sketchOf(doc().project)).toHaveLength(0);
  });
});

describe("editing, persistence and compatibility", () => {
  it("keeps construction curves until the user marks them as domain outlines", () => {
    draw(square().map((e) => ({ ...e, construction: true })));
    expect(doc().project.geometry.regions).toHaveLength(0);
    doc().update("domain outlines", (d) => { sketchOf(d).forEach((e) => { e.construction = false; }); });
    expect(doc().project.geometry.regions).toHaveLength(1);
    expect(sketchOf(doc().project)).toHaveLength(0);
  });

  it("does not use construction curves to close domain outlines", () => {
    const edges = square();
    draw([...edges.slice(0, 3), { ...edges[3], construction: true }]);
    expect(doc().project.geometry.regions).toHaveLength(0);
  });

  it("keeps layers separate and inherits the active layer on promotion", () => {
    draw(square().slice(0, 3));
    doc().update("layer", (d) => { const layer = addLayer(d); setActiveLayer(d, layer); });
    draw([square()[3]]);
    expect(doc().project.geometry.regions).toHaveLength(0);
    draw(square().slice(0, 3));
    expect(doc().project.geometry.regions[0].layer).toBe("L1");
    expect(sketchOf(doc().project)).toHaveLength(3);
  });

  it("does not modify locked-layer sketches or promote from an unrelated settings edit", () => {
    const p = produce(newProject(), (d) => {
      const layer = addLayer(d);
      square().forEach((e) => addSketch(d, { ...e, layer }));
      updateLayer(d, layer, { locked: true });
    });
    doc().replace(p, null);
    doc().update("mesh", (d) => { d.mesh.size = 0.0004; });
    expect(doc().project.geometry.regions).toHaveLength(0);
    expect(sketchOf(doc().project)).toHaveLength(4);
  });

  it("makes connection, promotion and automatic computational bounds one undoable edit", () => {
    draw(square().slice(0, 3));
    useSelection.getState().pick([{ kind: "sketch", id: "s3" }]);
    const before = doc().project;
    draw([square()[3]]);
    const closed = doc().project;
    expect(useSelection.getState().selectedRegion).toBe("region1");
    expect(useSelection.getState().picked).toEqual([{ kind: "region", id: "region1" }]);
    doc().undo();
    expect(doc().project).toEqual(before);
    expect(doc().project.geometry.regions).toHaveLength(0);
    doc().redo();
    expect(doc().project).toEqual(closed);
    doc().update("material", (d) => { d.geometry.regions[0].type = "dielectric"; d.geometry.regions[0].eps_r = 4; });
    expect(doc().project.geometry.regions[0].eps_r).toBe(4);
  });

  it("keeps a manually fixed computational outline and its boundary conditions", () => {
    const p = newProject();
    p.geometry.computational_domain!.mode = "manual";
    doc().replace(p, null);
    draw(square());
    expect(doc().project.geometry.domain).toEqual(p.geometry.domain);
    expect(doc().project.geometry.boundaries).toEqual(p.geometry.boundaries);
  });

  it("keeps unfinished domain outlines and construction flags across save/reopen", () => {
    draw(square().slice(0, 3));
    draw([{ kind: "circle", center: [0.08, 0.02], r: 0.005, construction: true }]);
    const saved = JSON.parse(serializeProject(doc().project));
    const loaded = normalizeProject(saved).project;
    expect(sketchOf(loaded).map((e) => e.construction)).toEqual([false, false, false, true]);
    doc().replace(loaded, null);
    draw([square()[3]]);
    expect(doc().project.geometry.regions).toHaveLength(1);
    expect(sketchOf(doc().project)).toMatchObject([{ kind: "circle", construction: true }]);
    expect(normalizeProject(JSON.parse(serializeProject(doc().project))).project.geometry.regions).toEqual(doc().project.geometry.regions);
  });

  it("preserves legacy sketches as construction curves without changing their physical geometry", () => {
    const legacy = produce(newProject(), (d) => {
      square().forEach((e) => addSketch(d, e));
      sketchOf(d).forEach((e) => { delete e.construction; });
    });
    const loaded = normalizeProject(legacy).project;
    expect(sketchOf(loaded).every((e) => e.construction)).toBe(true);
    doc().replace(loaded, null);
    draw([line([0.08, 0.01], [0.08, 0.02])]);
    expect(doc().project.geometry.regions).toHaveLength(0);
    expect(sketchOf(doc().project)).toHaveLength(5);
  });

  it("rejects nonfinite coordinates and bulges before topology detection", () => {
    draw([line([NaN, 0], [1, 1]), { kind: "arc", a: [0, 0], b: [1, 1], bulge: Infinity }, { kind: "circle", center: [0, 0], r: Infinity }]);
    expect(sketchOf(doc().project)).toHaveLength(0);
    expect(doc().project.geometry.regions).toHaveLength(0);
  });
});
