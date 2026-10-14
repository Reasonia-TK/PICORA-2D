// 2D 流体プラズマの設定ページ。直交格子と埋込境界を共通形状から使う。

import { useTranslation } from "react-i18next";
import { Section } from "../../forms/blocks";
import { SchemaField } from "../../forms/SchemaField";
import { useDocument } from "../../model/documentStore";
import { defaultFluid2d } from "../../schema/defaults";
import { projectFreqs } from "../../util/runHints";
import { BoltzSection } from "../widgets/BoltzSection";
import { ConvergenceSection } from "../widgets/ConvergenceSection";
import { Hint } from "../widgets/common";
import { ProcessList } from "../widgets/ProcessList";
import { PhaseBinHint, RfCycleHint } from "../widgets/RunHints";
import { RunControls } from "../widgets/RunControls";
import { StudyShell } from "./StudyShell";
import type { Project } from "../../model/project";

interface RunBlock { dt?: number | null; n_steps?: number; avg_steps?: number | null; phase_bins?: number; ion_mobility_model?: string; }

function boltzMissing(p: Project, key: "fluid2d"): boolean {
  const b = p[key] as { electron_model?: string; boltz_table?: unknown } | null | undefined;
  return b?.electron_model === "boltzmann" && !b.boltz_table;
}

/** 流体 1D・2D で共通の物理 (初期値・イオン・ガス・電子の断面積・係数) */
function FluidPhysics({ base }: { base: "fluid2d" }) {
  const { t } = useTranslation();
  const blk = (useDocument((s) => s.project[base]) ?? {}) as RunBlock;
  const P = [base] as const;
  return (
    <>
      <Section title={t("fluidPage.initial")}>
        <SchemaField path={[...P, "init_density_m3"]} />
        <SchemaField path={[...P, "init_te_ev"]} />
        <SchemaField path={[...P, "ion_mass_amu"]} />
        <SchemaField path={[...P, "t_i_ev"]} />
        <SchemaField path={[...P, "mu_i_ref"]} />
        <SchemaField path={[...P, "n_ref_m3"]} />
        <SchemaField path={[...P, "ion_mobility_model"]} />
        {blk.ion_mobility_model !== "const" && <SchemaField path={[...P, "frost_c_td"]} />}
        <Hint>{t("fluidPage.frostHint")}</Hint>
      </Section>
      <Section title={t("fluidPage.gas")}>
        <SchemaField path={[...P, "gas_pressure_pa"]} />
        <SchemaField path={[...P, "gas_temperature_k"]} />
      </Section>
      <Section title={t("fluidPage.xs")}>
        <ProcessList path={[...P, "electron_processes"]} species="electron" emptyHint={t("fluidPage.defaultXs")} />
      </Section>
      <Section title={t("fluidPage.coefficients")}>
        <BoltzSection path={P} />
      </Section>
    </>
  );
}

export function Fluid2dPage() {
  const { t } = useTranslation();
  const project = useDocument((s) => s.project);
  const blk = (project.fluid2d ?? {}) as RunBlock;
  const freqs = projectFreqs(project, true);
  const P = ["fluid2d"] as const;
  return (
    <StudyShell settingsKey="fluid2d" defaults={defaultFluid2d} description={t("fluidPage.description2d")} run={<RunControls kind="fluid2d" blocked={boltzMissing(project, "fluid2d") ? t("widgets.boltzNoTable") : null} />}>
      <Hint>{t("fluidPage.geometryFromProject")}</Hint>
      <FluidPhysics base="fluid2d" />
      <Section title={t("studyCommon.run")}>
        <Hint>{t("fluidPage.gpuSolverHint")}</Hint>
        <SchemaField path={[...P, "dt"]} />
        <RfCycleHint kind="fluid2d" freqs={freqs} dt={blk.dt} nSteps={blk.n_steps ?? 20000} />
        <SchemaField path={[...P, "n_steps"]} />
        <SchemaField path={[...P, "frame_every"]} />
        <SchemaField path={[...P, "avg_steps"]} placeholder={t("studyCommon.last25")} />
        <SchemaField path={[...P, "phase_bins"]} />
        <PhaseBinHint kind="fluid2d" freqs={freqs} dt={blk.dt} bins={blk.phase_bins ?? 0} avgSteps={blk.avg_steps} nSteps={blk.n_steps ?? 20000} />
      </Section>
      <ConvergenceSection base={P} kind="fluid" />
    </StudyShell>
  );
}
