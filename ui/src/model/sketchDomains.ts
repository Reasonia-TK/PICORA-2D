// 閉じたスケッチを計算用のドメイン（パーツ）に昇格する。
// 線・円弧の向きや描いた順序に依存せず、閉じた面を探す。元の輪郭だけを消し、
// はみ出した線や閉じていない枝はスケッチとして残す。外側の計算領域は別に同期する。
import type { Draft } from "immer";
import { Arrangement, splitAtIntersections, toleranceOf } from "../cad/arrangement";
import { ringsOf, shapeProblem } from "../cad/boolean";
import { chainToPath } from "../cad/edit";
import { closestPoint, pathSegs, pointAt, type Seg } from "../cad/geom";
import { itemLayer, keepLayer, layerOf } from "./layers";
import { paramsOf, removeBinding } from "./params";
import type { Point, Project } from "./project";
import { addCircleRegionAt, addShapeRegion } from "./regionOps";
import { addSketch, deleteSketch, isValidSketch, replaceSketch, sketchFromEditPath, sketchOf, sketchSegs, type SketchEntity } from "./sketch";

export interface SketchDomainPromotion {
  regionId: string;
  sketchIds: string[];
}

/** 見た目の属性の変更だけでは昇格しない。 */
function geometryKey(e: SketchEntity): string {
  const { id: _id, layer: _layer, ...shape } = e;
  return JSON.stringify(shape);
}

/** 端点だけでなく中点も比較するので、同じ端点の直線と円弧を区別できる。 */
function covered(s: Seg, boundaries: Seg[], tol: number): boolean {
  return boundaries.some((b) => [s.a, pointAt(s, 0.5), s.b].every((q) => closestPoint(b, q).dist <= tol));
}

function remainingChains(pieces: Seg[], used: boolean[], closed: boolean): Seg[][] {
  const chains: Seg[][] = [];
  let chain: Seg[] = [];
  pieces.forEach((s, i) => {
    if (used[i]) {
      if (chain.length) chains.push(chain);
      chain = [];
    } else chain.push(s);
  });
  if (chain.length) chains.push(chain);
  if (closed && chains.length > 1 && !used[0] && !used[used.length - 1]) {
    const last = chains.pop()!;
    chains[0] = [...last, ...chains[0]];
  }
  return chains;
}

/** 閉じた部分だけ消す。1本の長い線の途中が輪郭になった場合も両端を残す。 */
function consume(d: Draft<Project>, e: SketchEntity, pieces: Seg[], boundaries: Seg[], tol: number): void {
  const used = pieces.map((s) => covered(s, boundaries, tol));
  if (!used.some(Boolean)) return;
  // 消した・分けた輪郭の式を古い頂点番号で残さない。
  for (const b of paramsOf(d as Project).bindings.slice()) {
    if (b.path[0] === "cad" && b.path[1] === "sketch" && typeof b.path[2] === "object" && "id" in b.path[2] && b.path[2].id === e.id) removeBinding(d, b.path);
  }
  const closed = e.kind === "circle" || (e.kind === "polyline" && e.closed);
  const chains = remainingChains(pieces, used, closed);
  if (!chains.length) {
    deleteSketch(d, [e.id]);
    return;
  }
  chains.forEach((chain, i) => {
    const path = chainToPath(chain);
    const next = keepLayer(e, { ...sketchFromEditPath({ points: path.points as Point[], bulges: path.bulges, closed: false }), construction: e.construction });
    if (i === 0) replaceSketch(d, e.id, next);
    else addSketch(d, next);
  });
}

/** 編集されたスケッチのあるレイヤだけを調べる。読み込みや無関係な設定変更で旧スケッチを変換しない。 */
export function promoteClosedSketches(d: Draft<Project>, base: Project): SketchDomainPromotion[] {
  const p = d as Project;
  const before = new Map(sketchOf(base).map((e) => [e.id, geometryKey(e)]));
  const eligible = sketchOf(p).filter((e) => e.construction !== true && isValidSketch(e) && !layerOf(p, itemLayer(p, e)).locked);
  const changed = new Set(eligible.filter((e) => before.get(e.id) !== geometryKey(e)).map((e) => e.id));
  if (!changed.size) return [];
  const groups = new Map<string, SketchEntity[]>();
  eligible.forEach((e) => {
    const layer = itemLayer(p, e);
    groups.set(layer, [...(groups.get(layer) ?? []), e]);
  });
  const promoted: SketchDomainPromotion[] = [];
  for (const entities of groups.values()) {
    if (!entities.some((e) => changed.has(e.id))) continue;
    const segs = entities.flatMap(sketchSegs);
    const tol = toleranceOf(segs);
    const arrangement = new Arrangement(segs, tol);
    // 元の曲線を交点で分け、昇格する面にどのスケッチが寄与したかを記録する。
    const split = splitAtIntersections(segs, tol);
    let offset = 0;
    const sources = entities.map((e) => {
      const count = sketchSegs(e).length;
      const pieces = split.slice(offset, offset + count).flat();
      offset += count;
      return { e, pieces };
    });
    const consumed: Seg[] = [];
    for (const shape of arrangement.shapes()) {
      if (shapeProblem(shape) !== null) continue;
      const boundaries = ringsOf(shape).flatMap((r) => pathSegs(r.polygon, r.bulges));
      const contributing = sources.filter((s) => s.pieces.some((piece) => covered(piece, boundaries, tol * 2)));
      if (!contributing.some((s) => changed.has(s.e.id))) continue;
      // 単独の円は円のまま保存し、半径ハンドルも使えるようにする。
      const circle = contributing.length === 1 && contributing[0].e.kind === "circle" && !shape.holes.length &&
        contributing[0].pieces.every((s) => covered(s, boundaries, tol * 2)) ? contributing[0].e : null;
      const regionId = circle?.kind === "circle" ? addCircleRegionAt(d, circle.center, circle.r) : addShapeRegion(d, shape);
      if (!regionId) continue;
      keepLayer(entities[0], d.geometry.regions.find((r) => r.id === regionId)!);
      promoted.push({ regionId, sketchIds: contributing.map((s) => s.e.id) });
      consumed.push(...boundaries);
    }
    if (consumed.length) sources.forEach(({ e, pieces }) => consume(d, e, pieces, consumed, tol * 2));
  }
  return promoted;
}
