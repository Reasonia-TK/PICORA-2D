import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useConnection } from "../src/backend/connection";
import { GraphicsPanel } from "../src/graphics/GraphicsPanel";
import { useViewer } from "../src/graphics/viewerStore";
import { useDocument } from "../src/model/documentStore";
import { newProject } from "../src/model/project";
import { useSelection } from "../src/model/selection";
import { SettingsPanel } from "../src/pages/SettingsPanel";
import { useStatic } from "../src/results/staticResults";

beforeEach(() => {
  useDocument.getState().replace(newProject(), null, { untitledName: "test" });
  useViewer.setState({ tool: "select", profile: null, probe: null });
  useSelection.setState({ activeNode: "mesh", selectedRegion: null, selectedPlacement: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("viewer", () => {
  it("switches tools from the toolbar and explains missing WebGL", () => {
    render(<GraphicsPanel />);
    const rect = screen.getByRole("button", { name: "矩形" });
    fireEvent.click(rect);
    expect(useViewer.getState().tool).toBe("rect");
    expect(rect.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/1 つ目の角をクリック/)).toBeTruthy();
    // 場が無いとプローブは使えない
    expect((screen.getByRole("button", { name: "プローブ" }) as HTMLButtonElement).disabled).toBe(true);
    // jsdom には WebGL が無い
    expect(screen.getByText(/WebGL2 が使えない/)).toBeTruthy();
  });

  it("shows the line profile from /profile with a CSV button", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ s: [0, 0.05, 0.1], v: [0, 50, 100], e_abs: [1000, 1000, null] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<GraphicsPanel />);
    act(() => useViewer.getState().setProfile([[0, 0.025], [0.1, 0.025]]));
    expect(screen.getByText("ラインプロファイル")).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("button", { name: "CSV 保存" }) as HTMLButtonElement).disabled).toBe(false));
    const body = JSON.parse((fetchMock.mock.calls[0] as unknown as [string, { body: string }])[1].body);
    expect(body.p1).toEqual([0, 0.025]);
    expect(body.n).toBe(200);
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(useViewer.getState().profile).toBeNull();
  });
});

describe("Cartesian mesh page", () => {
  it("disables generation while disconnected and reports the generated mesh", async () => {
    useConnection.setState({ status: "disconnected" });
    render(<SettingsPanel />);
    expect((screen.getByRole("button", { name: "メッシュを作成" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("バックエンドに接続していないので計算できません")).toBeTruthy();
    const mesh = { nodes: [[0, 0], [0.1, 0], [0.1, 0.05], [0, 0.05]], triangles: [[0, 1, 2], [0, 2, 3]], region_of_triangle: [-1, -1] };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(mesh), { status: 200 })));
    useConnection.setState({ status: "connected" });
    await act(async () => { await useStatic.getState().runMesh(); });
    expect(screen.getByText("節点 4・要素 2")).toBeTruthy();
    act(() => useDocument.getState().update("mesh", (d) => void (d.mesh.size = 0.002)));
    expect(screen.getByText(/計算のあとで設定が変わっています/)).toBeTruthy();
    act(() => useStatic.getState().clear());
  });
});
