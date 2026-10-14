// メッシュ: 幅・方式、直交格子の局所細分化 (AMR)、辺の局所メッシュサイズ (非構造のみ)。

import { useTranslation } from "react-i18next";
import { OptionalBlock } from "../forms/blocks";
import { SchemaField, Toggle } from "../forms/SchemaField";
import { setValue } from "../forms/useField";
import { useDocument } from "../model/documentStore";
import { domainBounds, type Point } from "../model/project";
import { DEFAULT_AMR } from "../schema/defaults";
import { Hint } from "./widgets/common";
import { ListEditor } from "./widgets/ListEditor";
import { MeshBuild } from "./widgets/StaticRun";
import { Field } from "./inputs";

interface Amr {
  max_level?: number;
  blocking_factor?: number;
  adaptive?: boolean;
  pic_regrid_every?: number;
}

function AmrEditor() {
  const { t } = useTranslation();
  const project = useDocument((s) => s.project);
  const amr = (project.mesh.amr ?? null) as Amr | null;
  const base = ["mesh", "amr"] as const;
  const b = domainBounds(project);
  const label = t("mesh.amr");
  return (
    <OptionalBlock path={base} title={label} defaults={() => structuredClone(DEFAULT_AMR)} hint={t("mesh.amrHint")}>
      <SchemaField path={[...base, "max_level"]} />
      <SchemaField path={[...base, "refine_boundaries"]} />
      <SchemaField path={[...base, "buffer_cells"]} />
      <Field label={t("mesh.blockingFactor")}>
        {(id) => (
          <select id={id} className="input" value={String(amr?.blocking_factor ?? 8)} onChange={(e) => setValue([...base, "blocking_factor"], Number(e.target.value), label)}>
            {[4, 8, 16].map((n) => (
              <option key={n} value={n}>
                {n}×{n}
              </option>
            ))}
          </select>
        )}
      </Field>
      <SchemaField path={[...base, "adaptive"]} />
      {amr?.adaptive && (
        <>
          <SchemaField path={[...base, "adapt_tol"]} />
          <SchemaField path={[...base, "adapt_iters"]} />
        </>
      )}
      <div className="field field-toggle">
        <Toggle checked={(amr?.pic_regrid_every ?? 0) > 0} onChange={(on) => setValue([...base, "pic_regrid_every"], on ? 500 : 0, label)} label={t("mesh.picRegrid")} />
      </div>
      {(amr?.pic_regrid_every ?? 0) > 0 && (
        <>
          <SchemaField path={[...base, "pic_regrid_every"]} />
          <SchemaField path={[...base, "pic_h_over_debye"]} />
        </>
      )}
      <div className="subsection-title">{t("mesh.amrRegions")}</div>
      <ListEditor<{ level: number }>
        path={[...base, "regions"]}
        label={t("mesh.amrRegions")}
        title={(r, i) => `R${i + 1} · L${r.level}`}
        emptyText={t("mesh.amrRegionsEmpty")}
        create={() => {
          // 中央に幅・高さとも 1/4 の矩形 (v1 と同じ: 0.375〜0.625)
          const cx = (b.x0 + b.x1) / 2;
          const cy = (b.y0 + b.y1) / 2;
          const hw = (b.x1 - b.x0) / 8;
          const hh = (b.y1 - b.y0) / 8;
          return { p1: [cx - hw, cy - hh] as Point, p2: [cx + hw, cy + hh] as Point, level: Math.max(1, amr?.max_level ?? 1) };
        }}
        render={(_, i) => (
          <>
            <SchemaField path={[...base, "regions", i, "p1"]} />
            <SchemaField path={[...base, "regions", i, "p2"]} />
            <SchemaField path={[...base, "regions", i, "level"]} />
          </>
        )}
      />
      <Hint>{t("mesh.amrExplain")}</Hint>
    </OptionalBlock>
  );
}

export function MeshPage() {
  const { t } = useTranslation();
  return (
    <>
      <Hint>{t("mesh.cartesianOnly")}</Hint>
      <SchemaField path={["mesh", "size"]} />
      <Hint>{t("mesh.modeHint.cartesian")}</Hint>
      <MeshBuild />
      <AmrEditor />
    </>
  );
}
