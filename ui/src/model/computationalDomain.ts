// パーツ先行の計算領域。実形状から余白付き矩形を作り、外周の辺IDと境界条件を引き継ぐ。
import type { Draft } from "immer";
import { pathBounds } from "../cad/geom";
import { bulgesOf } from "../cad/path";
import { applyDomainEdit, boundaryValueKey, type DomainEdit } from "./domainOps";
import type { Params } from "./params";
import { axisEdges, coordOf, domainBounds, domainPath, edgeIdsOf, nextEdgeId, regionPath, tidyPoint, type Bounds, type ComputationalDomain, type Point, type Project } from "./project";

export const DEFAULT_DOMAIN_MARGIN = 0.005;
export class RectangleBoundaryConflictError extends Error {
  constructor(public readonly edges: string[]) {
    super("旧外周の分割辺に異なる境界条件・PIC反射設定があります。計算領域の矩形への変換を中止しました。境界条件を同じ値にそろえてから再実行してください。");
  }
}

const DEFAULT_BOUNDS: Bounds = { x0: 0, y0: 0, x1: 0.1, y1: 0.05 };

export function computationalDomainOf(p: Project): ComputationalDomain {
  return p.geometry.computational_domain ?? { mode: "manual", margin: DEFAULT_DOMAIN_MARGIN };
}

/** スケッチやレイヤ表示状態ではなく、計算に使う全パーツの実形状（円弧込み）。 */
export function partsBounds(p: Project): Bounds | null {
  let out: Bounds | null = null;
  for (const r of p.geometry.regions) {
    const path = regionPath(r);
    if (path.polygon.length < 2) continue;
    const b = pathBounds(path.polygon, path.bulges);
    if (![b.x0, b.x1, b.y0, b.y1].every(Number.isFinite)) continue;
    out = out ? { x0: Math.min(out.x0, b.x0), y0: Math.min(out.y0, b.y0), x1: Math.max(out.x1, b.x1), y1: Math.max(out.y1, b.y1) } : b;
  }
  return out;
}

export function followedDomainBounds(p: Project): Bounds {
  const parts = partsBounds(p);
  const rawMargin = computationalDomainOf(p).margin;
  const margin = Number.isFinite(rawMargin) && rawMargin >= 0 ? rawMargin : DEFAULT_DOMAIN_MARGIN;
  const b = parts ? { x0: parts.x0 - margin, y0: parts.y0 - margin, x1: parts.x1 + margin, y1: parts.y1 + margin } : { ...DEFAULT_BOUNDS };
  // 軸を跨ぐパーツは動かさず、径方向が負の部分は計算領域の外にする。
  if (coordOf(p) === "rz") b.y0 = Math.max(0, b.y0);
  if (coordOf(p) === "rz_x0") b.x0 = Math.max(0, b.x0);
  b.x1 = Math.max(b.x1, b.x0 + 1e-6);
  b.y1 = Math.max(b.y1, b.y0 + 1e-6);
  return b;
}

export function partsCrossAxis(p: Project): boolean {
  const b = partsBounds(p);
  return !!b && (coordOf(p) === "rz" ? b.y0 < -1e-12 : coordOf(p) === "rz_x0" && b.x0 < -1e-12);
}

/** 旧矩形の開始頂点・向きに依らず、下/右/上/左へ旧辺IDを付け替える。 */
export function rectangleDomainEdit(p: Project, b: Bounds): DomainEdit {
  const corners: Point[] = [[b.x0, b.y0], [b.x1, b.y0], [b.x1, b.y1], [b.x0, b.y1]];
  const polygon = corners.map(tidyPoint);
  const old = domainPath(p);
  const oldBounds = domainBounds(p);
  const oldIds = edgeIdsOf(p);
  const bulges = bulgesOf(old);
  const tol = 1e-12 * Math.max(1, oldBounds.x1 - oldBounds.x0, oldBounds.y1 - oldBounds.y0);
  const near = (a: number, c: number) => Math.abs(a - c) <= tol;
  const sides: string[][] = [[], [], [], []];
  old.polygon.forEach((a, i) => {
    if (bulges[i]) return;
    const c = old.polygon[(i + 1) % old.polygon.length];
    const side = near(a[1], oldBounds.y0) && near(c[1], oldBounds.y0) ? 0
      : near(a[0], oldBounds.x1) && near(c[0], oldBounds.x1) ? 1
      : near(a[1], oldBounds.y1) && near(c[1], oldBounds.y1) ? 2
      : near(a[0], oldBounds.x0) && near(c[0], oldBounds.x0) ? 3 : -1;
    if (side >= 0) sides[side].push(oldIds[i]);
  });
  // 一辺へ潰す旧分割辺が異なる条件なら、物理設定を変えることになるので変換しない。
  const params = p.params as Partial<Params> | null | undefined;
  const signature = (id: string) => {
    const i = oldIds.indexOf(id);
    const conditions = p.geometry.boundaries.filter((b) => b.edges.includes(i));
    if (new Set(conditions.map(boundaryValueKey)).size > 1) throw new RectangleBoundaryConflictError([id]);
    const bc = conditions[0];
    const bound = (params?.bindings ?? []).filter((binding) => {
      const edge = binding.path[2];
      return binding.path[0] === "geometry" && binding.path[1] === "boundaries" && typeof edge === "object" && "edge" in edge && bc?.edges.some((e) => oldIds[e] === edge.edge);
    }).map((binding) => JSON.stringify([binding.path.slice(3), binding.expr])).sort();
    return JSON.stringify([boundaryValueKey(bc ?? { type: "neumann", edges: [] }), bound, Array.isArray(p.pic?.reflect_edges) && p.pic.reflect_edges.includes(i)]);
  };
  for (const side of sides) {
    if (side.length > 1 && new Set(side.map(signature)).size > 1) throw new RectangleBoundaryConflictError(side);
  }
  const all = [...oldIds];
  const ids = sides.map((side) => {
    if (side.length) return side[0];
    const id = nextEdgeId(all);
    all.push(id);
    return id;
  });
  return { path: { polygon, bulges: null }, ids, mergeEquivalentBoundaries: true, sources: Object.fromEntries(ids.map((id, i) => [id, sides[i]])) };
}

/** 自動生成の座標を旧パラメータの束縛で次回上書きしない。変数やパーツの束縛は保持。 */
export function clearAutoDomainBindings(p: Project): void {
  if (computationalDomainOf(p).mode !== "auto") return;
  const params = p.params as Partial<Params> | null | undefined;
  if (params?.bindings?.some((b) => b.path[0] === "geometry" && b.path[1] === "domain")) {
    params.bindings = params.bindings.filter((b) => !(b.path[0] === "geometry" && b.path[1] === "domain"));
  }
}

export function syncComputationalDomain(d: Draft<Project> | Project): void {
  const p = d as Project;
  if (computationalDomainOf(p).mode !== "auto") return;
  const edit = rectangleDomainEdit(p, followedDomainBounds(p));
  const dom = p.geometry.domain;
  const axis = new Set(axisEdges(p));
  const needsAxisCleanup = p.geometry.boundaries.some((bc) => bc.edges.some((e) => axis.has(e)));
  if (!needsAxisCleanup && !dom.bulges?.some((x) => x) && JSON.stringify(dom.polygon) === JSON.stringify(edit.path.polygon) && JSON.stringify(edgeIdsOf(p)) === JSON.stringify(edit.ids)) return;
  applyDomainEdit(d as Draft<Project>, edit);
}

export function setComputationalDomainMode(d: Draft<Project>, mode: ComputationalDomain["mode"]): void {
  d.geometry.computational_domain = { ...computationalDomainOf(d as Project), mode };
  clearAutoDomainBindings(d);
}
