// 名前付きセットを保存し、計算へ適用すると全過程と出典をプロジェクトへ埋め込む。
import { useEffect, useState, type FormEvent } from "react";
import { apiGet, apiPost } from "../backend/api";
import { useConnection } from "../backend/connection";
import { useDocument } from "../model/documentStore";
import { Field } from "./inputs";
import { BoltzGenerate } from "./widgets/BoltzGenerate";
import { Hint } from "./widgets/common";
import "./crossSections.css";

const KINDS = ["ELASTIC", "EFFECTIVE", "EXCITATION", "IONIZATION", "ATTACHMENT", "ROTATION", "ISOTROPIC", "BACKSCAT"];
const KIND_LABELS: Record<string, string> = { ELASTIC: "弾性", EFFECTIVE: "有効運動量移行", EXCITATION: "励起", IONIZATION: "電離", ATTACHMENT: "付着", ROTATION: "回転励起", ISOTROPIC: "イオン等方散乱", BACKSCAT: "イオン後方散乱" };
interface Entry { id: string; gas: string; source: string; source_url: string; notes: string; mass_amu: number | null; cross_section: { kind: string; projectile: string; label: string; threshold_ev: number; energy_ev: number[] } }
interface XsSet { id: string; name: string; entries: Entry[]; warnings: string[] }
interface Listing { sets: XsSet[]; storage_path: string }
interface ImportResult { set: XsSet; added: number; warnings: string[] }
interface Resolved { gas: string; mass_amu: number; processes: Record<string, unknown>[]; warnings: string[]; sources: unknown[]; set_id: string; set_name: string }
interface CsvFile { key: string; filename: string; text: string; kind: string; threshold: string; projectile: string; label: string }

function number(value: string, name: string, positive = false): number {
  const result = Number(value);
  if (!value.trim() || !Number.isFinite(result) || (positive ? result <= 0 : result < 0)) throw new Error(`${name} は有限の${positive ? "正の" : "0 以上の"}数で指定してください`);
  return result;
}
async function readText(file: File): Promise<string> {
  if (file.size > 8 * 1024 * 1024) throw new Error("ファイルは 8 MiB 以下にしてください");
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder("utf-16le").decode(bytes);
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder("utf-16be").decode(bytes);
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { return new TextDecoder("windows-1252").decode(bytes); }
}
const kindOptions = () => KINDS.map((kind) => <option key={kind} value={kind}>{KIND_LABELS[kind]} · {kind}</option>);

export function CrossSectionsPage() {
  const connected = useConnection((s) => s.status === "connected");
  const picEnabled = useDocument((s) => Boolean(s.project.pic));
  const fluidEnabled = useDocument((s) => Boolean(s.project.fluid2d));
  const [listing, setListing] = useState<Listing>({ sets: [], storage_path: "" });
  const [selectedId, setSelectedId] = useState("");
  const [entryIds, setEntryIds] = useState<string[]>([]);
  const [renameName, setRenameName] = useState("");
  const [destination, setDestination] = useState("new");
  const [setName, setSetName] = useState("");
  const [copyDestination, setCopyDestination] = useState("new");
  const [copyName, setCopyName] = useState("");
  const [format, setFormat] = useState("lxcat");
  const [text, setText] = useState("");
  const [csvFiles, setCsvFiles] = useState<CsvFile[]>([]);
  const [source, setSource] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [notes, setNotes] = useState("");
  const [gas, setGas] = useState("Ar");
  const [kind, setKind] = useState("ELASTIC");
  const [projectile, setProjectile] = useState("e");
  const [threshold, setThreshold] = useState("0");
  const [mass, setMass] = useState("");
  const [label, setLabel] = useState("");
  const [pressure, setPressure] = useState("10");
  const [temperature, setTemperature] = useState("300");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const selected = listing.sets.find((item) => item.id === selectedId);
  const unavailable = !connected || busy;
  const refresh = async () => {
    const next = await apiGet<Listing>("/xs/registry"); setListing(next);
    setSelectedId((id) => next.sets.some((item) => item.id === id) ? id : "");
    setDestination((id) => id === "new" || next.sets.some((item) => item.id === id) ? id : "new");
  };
  useEffect(() => {
    if (!connected) return;
    let alive = true;
    void apiGet<Listing>("/xs/registry").then((next) => { if (alive) setListing(next); }).catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [connected]);
  useEffect(() => { setEntryIds([]); setRenameName(selected?.name ?? ""); setCopyName(selected ? `${selected.name} コピー` : ""); setCopyDestination("new"); }, [selectedId, selected?.name]);
  const action = async (run: () => Promise<void>) => {
    setBusy(true); setError(null); setMessage(null); setWarnings([]);
    try { await run(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const afterImport = async (result: ImportResult) => {
    await refresh(); setSelectedId(result.set.id); setDestination(result.set.id); setEntryIds([]); setWarnings(result.warnings);
    setMessage(`「${result.set.name}」へ ${result.added} 過程を登録しました（合計 ${result.set.entries.length} 過程）。`);
  };
  const register = async (event: FormEvent) => {
    event.preventDefault();
    await action(async () => {
      if (!source.trim()) throw new Error("出典を入力してください");
      if (destination === "new" && !setName.trim()) throw new Error("セット名を入力してください");
      const meta = { source, source_url: sourceUrl, notes, ...(destination === "new" ? { set_name: setName } : { set_id: destination }) };
      if (format === "lxcat") {
        if (!text.trim()) throw new Error("LXCat ファイルを選択するか、テキストを入力してください");
        await afterImport(await apiPost<ImportResult>("/xs/registry/lxcat", { ...meta, text }));
      } else {
        const files = csvFiles.length ? csvFiles : [{ key: "text", filename: "", text, kind, threshold, projectile, label }];
        if (files.some((file) => !file.text.trim())) throw new Error("CSV ファイルを選択するか、テキストを入力してください");
        await afterImport(await apiPost<ImportResult>("/xs/registry/csv/batch", { ...meta, files: files.map((file) => ({ text: file.text, filename: file.filename, gas, kind: file.kind, projectile: file.projectile, label: file.label,
          threshold_ev: number(file.threshold, `${file.filename || "CSV"} の閾値 [eV]`), mass_amu: mass.trim() ? number(mass, "ガス質量 [amu]", true) : null })) }));
        setCsvFiles([]); setText("");
      }
    });
  };
  const apply = (target: "pic" | "fluid") => action(async () => {
    if (!selected) throw new Error("適用するセットを選択してください");
    const state = useDocument.getState(); const serial = state.docSerial;
    const p = number(pressure, "適用ガス圧 [Pa]", true); const t = number(temperature, "適用ガス温度 [K]", true);
    const hasElectron = selected.entries.some((entry) => entry.cross_section.projectile === "e" && ["ELASTIC", "EFFECTIVE", "EXCITATION", "IONIZATION", "ROTATION"].includes(entry.cross_section.kind));
    const hasIon = selected.entries.some((entry) => entry.cross_section.projectile !== "e" && ["ISOTROPIC", "BACKSCAT"].includes(entry.cross_section.kind));
    if (target === "fluid" && !hasElectron) throw new Error("セットに流体計算で使用できる電子衝突過程がありません");
    if (target === "pic" && !hasElectron && !hasIon) throw new Error("セットに PIC MCC で使用できる衝突過程がありません");
    const electron = hasElectron ? await apiPost<Resolved>("/xs/registry/resolve", { set_id: selected.id, species: "electron" }) : null;
    const ion = target === "pic" && hasIon ? await apiPost<Resolved>("/xs/registry/resolve", { set_id: selected.id, species: "ion" }) : null;
    const result = electron ?? ion!;
    if (useDocument.getState().docSerial !== serial) throw new Error("プロジェクトが切り替わりました。適用先を確認して再度実行してください");
    if (target === "fluid" && !electron?.processes.some((process) => process.kind === "elastic")) throw new Error("流体の電子係数には弾性（または有効運動量移行）断面積もセットへ追加してください");
    state.update("断面積セットを計算に適用", (draft) => {
      const binding = { id: result.set_id, name: result.set_name };
      if (target === "fluid") {
        if (!draft.fluid2d) throw new Error("先に 2D 流体の計算設定を有効にしてください");
        Object.assign(draft.fluid2d, { electron_processes: electron!.processes, gas_pressure_pa: p, gas_temperature_k: t, ion_mass_amu: result.mass_amu,
          boltz_table: null, electron_model: "maxwell", xs_registry_sources: result.sources, xs_registry_set: binding });
      } else {
        if (!draft.pic) throw new Error("先に 2D PIC の計算設定を有効にしてください");
        const current = (draft.pic.mcc ?? {}) as Record<string, unknown>;
        Object.assign(current, { gas: { name: result.gas, pressure_pa: p, temperature_k: t }, electron_processes: electron?.processes ?? [], ion_processes: ion?.processes ?? [],
          electron_xs_sources: electron?.sources ?? [], ion_xs_sources: ion?.sources ?? [], xs_registry_set: binding }); draft.pic.mcc = current;
        const initial = draft.pic.initial_plasma as Record<string, unknown> | null | undefined;
        if (initial) initial.ion_mass_amu = result.mass_amu;
      }
    });
    const allWarnings = [...(electron?.warnings ?? []), ...(ion?.warnings ?? [])];
    setWarnings([...new Set(target === "pic" ? allWarnings.filter((warning) => !warning.includes("species='")) : allWarnings)]);
    setMessage(`「${result.set_name}」を${target === "fluid" ? " 2D 流体" : " 2D PIC MCC"}へ適用しました。${target === "fluid" ? "電子係数は Maxwell 平均に設定しました。Boltzmann 係数を使用する場合は係数表を生成してください。イオン移動度はガスに合わせて確認してください。" : `電子 ${electron?.processes.length ?? 0} 過程、イオン ${ion?.processes.length ?? 0} 過程を置き換えました。`}`);
  });
  const deleteSelected = () => action(async () => { if (selected) { await apiPost("/xs/registry/delete", { set_ids: [selected.id] }); await refresh(); setMessage(`「${selected.name}」を削除しました。適用済みの配列と出典はプロジェクトに保存されています。`); } });
  const rename = () => action(async () => { if (selected) { await apiPost("/xs/registry/rename", { set_id: selected.id, name: renameName }); await refresh(); setMessage("セット名を更新しました。"); } });
  const copyProcesses = () => action(async () => { if (selected) await afterImport(await apiPost<ImportResult>("/xs/registry/copy", { source_set_id: selected.id, entry_ids: entryIds, ...(copyDestination === "new" ? { set_name: copyName } : { set_id: copyDestination }) })); });
  const removeProcesses = () => action(async () => { if (selected) { await apiPost("/xs/registry/remove-processes", { set_id: selected.id, entry_ids: entryIds }); await refresh(); setEntryIds([]); setMessage("選択した過程をセットから除外しました。"); } });

  return <div className="xs-registry">
    <h2>衝突断面積セット</h2><p className="muted">名前付きセットへ衝突過程をまとめ、セット全体を 2D PIC MCC と 2D 流体へ適用できます。</p>
    {!connected && <Hint tone="warn">ローカルバックエンドへ接続すると登録・一覧・適用を利用できます。</Hint>}
    {error && <div role="alert"><Hint tone="error">{error}</Hint></div>}{message && <div role="status"><Hint>{message}</Hint></div>}{warnings.map((warning, index) => <Hint key={index} tone="warn">{warning}</Hint>)}
    <form onSubmit={(event) => { void register(event); }} className="xs-registry-form">
      <h3>セットへ登録</h3>
      <Field label="登録先">{(id) => <select id={id} className="input" value={destination} disabled={busy} onChange={(event) => setDestination(event.target.value)}><option value="new">新しいセットを作成</option>{listing.sets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}</Field>
      {destination === "new" && <Field label="セット名">{(id) => <input id={id} className="input" value={setName} maxLength={256} onChange={(event) => setSetName(event.target.value)} required placeholder="例: Ar 電子・イオン衝突セット" />}</Field>}
      <Field label="形式">{(id) => <select id={id} className="input" value={format} disabled={busy} onChange={(event) => { setFormat(event.target.value); setText(""); setCsvFiles([]); }}><option value="lxcat">LXCat</option><option value="csv">CSV（eV / m²）</option></select>}</Field>
      <Field label={format === "csv" ? "CSV ファイル（複数可）" : "LXCat ファイル"}>{(id) => <input id={id} className="input" type="file" multiple={format === "csv"} accept={format === "csv" ? ".csv,text/csv,text/plain" : ".txt,.dat,text/plain"} disabled={busy} onChange={(event) => {
        const files = Array.from(event.target.files ?? []); event.target.value = "";
        if (files.length) void action(async () => { if (files.reduce((sum, file) => sum + file.size, 0) > 8 * 1024 * 1024) throw new Error("選択ファイルは合計 8 MiB 以下にしてください");
          if (format === "lxcat") setText(await readText(files[0])); else { const added = await Promise.all(files.map(async (file) => ({ key: crypto.randomUUID(), filename: file.name, text: await readText(file), kind, threshold, projectile, label: label || file.name.replace(/\.csv$/i, "") }))); setCsvFiles((current) => [...current, ...added]); } });
      }} />}</Field>
      {format === "csv" && <><Hint>CSV は energy[eV],sigma[m2] の 2 列です。複数ファイルは種別・閾値・入射粒子を指定し、一度に同じセットへ追加できます。</Hint>
        <Field label="ガス名">{(id) => <input id={id} className="input" value={gas} onChange={(event) => setGas(event.target.value)} required />}</Field><Field label="ガス質量（任意）" unit="amu">{(id) => <input id={id} className="input" inputMode="decimal" value={mass} onChange={(event) => setMass(event.target.value)} placeholder="既知のガスは自動補完" />}</Field></>}
      {format === "csv" && csvFiles.length ? <div className="xs-table-scroll"><table className="table xs-csv-table"><thead><tr><th>ファイル / 過程名</th><th>衝突種別</th><th>閾値 [eV]</th><th>入射粒子</th><th>操作</th></tr></thead><tbody>{csvFiles.map((file) => {
        const change = (patch: Partial<CsvFile>) => setCsvFiles((current) => current.map((item) => item.key === file.key ? { ...item, ...patch } : item));
        return <tr key={file.key}><td><div className="muted">{file.filename}</div><input className="input" aria-label={`${file.filename} 過程名`} value={file.label} onChange={(event) => change({ label: event.target.value })} /></td>
          <td><select className="input" aria-label={`${file.filename} 衝突種別`} value={file.kind} onChange={(event) => change({ kind: event.target.value, ...(["ELASTIC", "EFFECTIVE", "ISOTROPIC", "BACKSCAT"].includes(event.target.value) ? { threshold: "0" } : {}) })}>{kindOptions()}</select></td>
          <td><input className="input" aria-label={`${file.filename} 閾値`} inputMode="decimal" value={file.threshold} onChange={(event) => change({ threshold: event.target.value })} /></td><td><input className="input" aria-label={`${file.filename} 入射粒子`} value={file.projectile} onChange={(event) => change({ projectile: event.target.value })} /></td>
          <td><button type="button" className="button small" aria-label={`${file.filename} を取り除く`} disabled={busy} onClick={() => setCsvFiles((current) => current.filter((item) => item.key !== file.key))}>取り除く</button></td></tr>;
      })}</tbody></table></div> : <><label className="xs-text-label" htmlFor="xs-text">断面積テキスト</label><textarea id="xs-text" className="input xs-text" rows={7} value={text} onChange={(event) => setText(event.target.value)} placeholder={format === "csv" ? "ヘッダー: energy[eV],sigma[m2]\nデータ行: エネルギー,断面積" : "LXCat ファイルの内容を貼り付けることもできます"} />
        {format === "csv" && <><Field label="衝突種別">{(id) => <select id={id} className="input" value={kind} onChange={(event) => setKind(event.target.value)}>{kindOptions()}</select>}</Field><Field label="閾値" unit="eV">{(id) => <input id={id} className="input" inputMode="decimal" value={threshold} onChange={(event) => setThreshold(event.target.value)} required />}</Field><Field label="入射粒子">{(id) => <input id={id} className="input" value={projectile} onChange={(event) => setProjectile(event.target.value)} placeholder="電子: e / イオン: Ar^+" required />}</Field><Field label="過程名（任意）">{(id) => <input id={id} className="input" value={label} onChange={(event) => setLabel(event.target.value)} />}</Field></>}
      </>}
      {format === "lxcat" && <Hint>ファイル内の全過程をひとつのセットへ取り込みます。ガス・種別・閾値・単位はファイルから読み取ります。</Hint>}
      <Field label="出典">{(id) => <input id={id} className="input" value={source} onChange={(event) => setSource(event.target.value)} required placeholder="データベース、文献、測定元" />}</Field><Field label="出典 URL（任意）">{(id) => <input id={id} type="url" className="input" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" />}</Field><Field label="メモ（任意）">{(id) => <input id={id} className="input" value={notes} onChange={(event) => setNotes(event.target.value)} />}</Field>
      <div className="button-row"><button type="submit" className="button primary" disabled={unavailable}>{busy ? "処理中…" : destination === "new" ? "セットを登録" : "セットへ追加"}</button></div>
    </form>
    <h3>登録セット <span className="muted">({listing.sets.length})</span></h3><p className="muted xs-storage">保存先: {listing.storage_path || "接続後に表示"}<br />保存先は PICORA_XS_REGISTRY 環境変数で変更できます。旧登録は元データを .v1.bak へ保存してセットへ移行します。</p>
    <div className="button-row tight"><button type="button" className="button small" disabled={unavailable} onClick={() => { void action(refresh); }}>再読み込み</button><button type="button" className="button small danger" disabled={unavailable || !selected} onClick={() => { void deleteSelected(); }}>選択セットを削除</button></div>
    {!listing.sets.length ? <Hint>登録されたセットはありません。</Hint> : <div className="xs-set-list" role="radiogroup" aria-label="断面積セット選択">{listing.sets.map((item) => <label key={item.id} className={`xs-set-option ${selectedId === item.id ? "xs-selected" : ""}`}><input type="radio" name="xs-set" value={item.id} checked={selectedId === item.id} disabled={busy} onChange={() => setSelectedId(item.id)} /><span><strong>{item.name}</strong><span className="muted">{[...new Set(item.entries.map((entry) => entry.gas))].join(" / ")} · {item.entries.length} 過程</span></span></label>)}</div>}
    {selected && <section className="xs-set-details" aria-label="選択セットの内訳"><h3>「{selected.name}」の内訳</h3><Field label="選択セット名">{(id) => <input id={id} className="input" value={renameName} onChange={(event) => setRenameName(event.target.value)} />}</Field><button type="button" className="button small" disabled={unavailable || !renameName.trim()} onClick={() => { void rename(); }}>名前を更新</button>
      {(selected.warnings ?? []).map((warning, index) => <Hint key={index} tone="warn">{warning}</Hint>)}<div className="button-row tight"><button type="button" className="button small" disabled={busy} onClick={() => setEntryIds(entryIds.length === selected.entries.length ? [] : selected.entries.map((entry) => entry.id))}>{entryIds.length === selected.entries.length ? "過程選択を解除" : "内訳をすべて選択"}</button></div>
      <div className="xs-table-scroll"><table className="table xs-table"><thead><tr><th>編集対象</th><th>ガス / 入射粒子</th><th>種別 / 過程</th><th>閾値 [eV]</th><th>表の範囲 [eV] / 点数</th><th>出典</th></tr></thead><tbody>{selected.entries.map((entry) => <tr key={entry.id}>
        <td><input type="checkbox" aria-label={`${entry.cross_section.label} を編集対象にする`} checked={entryIds.includes(entry.id)} disabled={busy} onChange={(event) => setEntryIds((ids) => event.target.checked ? [...ids, entry.id] : ids.filter((id) => id !== entry.id))} /></td><td>{entry.gas}<div className="muted">{entry.cross_section.projectile}</div></td><td>{KIND_LABELS[entry.cross_section.kind]}<div className="muted">{entry.cross_section.label}</div></td><td>{entry.cross_section.threshold_ev}</td><td>{entry.cross_section.energy_ev[0]} – {entry.cross_section.energy_ev.at(-1)}<div className="muted">{entry.cross_section.energy_ev.length} 点</div></td><td>{entry.source_url ? <a href={entry.source_url} target="_blank" rel="noreferrer">{entry.source}</a> : entry.source}{entry.notes && <div className="muted">{entry.notes}</div>}</td>
      </tr>)}</tbody></table></div>
      <details className="xs-process-edit"><summary>既存過程を別セットへ追加・内訳を編集</summary><Hint>編集対象のチェックはコピー・除外に使用します。計算へはセット全体が適用されます。</Hint><Field label="過程の追加先">{(id) => <select id={id} className="input" value={copyDestination} onChange={(event) => setCopyDestination(event.target.value)}><option value="new">新しいセットへコピー</option>{listing.sets.filter((item) => item.id !== selected.id).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}</Field>{copyDestination === "new" && <Field label="コピー先セット名">{(id) => <input id={id} className="input" value={copyName} onChange={(event) => setCopyName(event.target.value)} />}</Field>}
        <div className="button-row"><button type="button" className="button" disabled={unavailable || !entryIds.length || (copyDestination === "new" && !copyName.trim())} onClick={() => { void copyProcesses(); }}>選択過程を追加 ({entryIds.length})</button><button type="button" className="button danger" disabled={unavailable || !entryIds.length || entryIds.length === selected.entries.length} onClick={() => { void removeProcesses(); }}>選択過程をセットから除外</button></div>
      </details></section>}
    <h3>セットを計算へ適用</h3><Hint>選択セットの全過程で現在の一覧を置き換え、ガス圧・温度も設定します。PIC では電子とイオンを一緒に適用します。セットは同じガスで構成してください。元に戻す操作ができます。</Hint>
    <Field label="適用ガス圧" unit="Pa">{(id) => <input id={id} className="input" inputMode="decimal" value={pressure} onChange={(event) => setPressure(event.target.value)} />}</Field><Field label="適用ガス温度" unit="K">{(id) => <input id={id} className="input" inputMode="decimal" value={temperature} onChange={(event) => setTemperature(event.target.value)} />}</Field>
    <div className="button-row"><button type="button" className="button" disabled={unavailable || !selected || !picEnabled} onClick={() => { void apply("pic"); }}>2D PIC MCC へセット適用</button><button type="button" className="button" disabled={unavailable || !selected || !fluidEnabled} onClick={() => { void apply("fluid"); }}>2D 流体 電子係数へセット適用</button></div>
    {(!picEnabled || !fluidEnabled) && <p className="muted">計算設定を有効にすると、その計算への適用ボタンを利用できます。</p>}<p className="muted">付着と超弾性過程は現在の計算エンジンで扱えず、適用時に警告を表示します。</p>{fluidEnabled && <BoltzGenerate module="fluid2d" />}
  </div>;
}
