import glob
import importlib.util
import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

binaries = []
# ---- hidden imports ------------------------------------------------------------
hiddenimports = [
    "es_sim",
    "es_sim.server",   # uvicorn.run("es_sim.server:app") の文字列参照
    "numba",           # _numba_kernels.py の try/except 内 import の保険 (prompts/76)
    "boltzpmp",        # boltz.py の try/except 内 import の保険 (numba と同じ理由、prompts/117/119)
    "boltzpmp._core",  # boltzpmp の Rust 拡張 (.pyd)。静的解析で拾われない場合の保険
]
# es_sim のモジュールは全部 (関数の中で import するもの、ジョブの実行器など)
hiddenimports += collect_submodules("es_sim")
# uvicorn のワーカ/ループ/プロトコル実装は動的インポートされる
hiddenimports += collect_submodules("uvicorn")
# v2 AMR の合成格子ソルバー (es_sim/amr/composite.py) は関数内で pyamg を import し、
# pyamg の C++ 拡張 (amg_core) はサブモジュールから読まれるため明示する (prompts/121)
hiddenimports += collect_submodules("pyamg", filter=lambda name: ".tests" not in name)
# DXF の読み書き (es_sim/dxf.py、prompts/132 P7g): ezdxf の C 拡張 (ezdxf.acc) は try の中で読まれるので明示する
# (無くても純 Python で動くが遅い)。ezdxf の同梱データ (GUI のアイコン・フォント) は読み書きに使わない
hiddenimports += collect_submodules("ezdxf.acc")

# boltzpmp 同梱の LXCat 断面積データ (boltzpmp/data/Ar.txt 等、load_argon() が参照する。
# 本体の LMEA テーブル生成 (xsprocess_to_mixture) 自体はこれらを使わないが、
# boltzpmp パッケージの一部として同梱しておく (prompts/117)
datas = collect_data_files("boltzpmp")
# v2 の CUDA カーネル (.cu) は実行時に NVRTC でコンパイルするためソースを同梱する (prompts/119)
datas += collect_data_files("es_sim", includes=["kernels/*.cu", "kernels/*.cuh"])

# ---- CuPy と NVRTC (prompts/133 P8b) ---------------------------------------------
#: 同梱しない CUDA の DLL (CuPy の拡張のリンク先として拾われる。GPU の計算は NVRTC だけで動く、P8a)
_CUDA_DLL = re.compile(
    r"^(cublas|cublaslt|cusolver|cusolvermg|cusparse|cufft|cufftw|curand|nvjitlink|cutensor|cutensormg|nvrtc|"
    r"nvrtc-builtins|cudart|nvjpeg|npp|nvblas|nvfatbin)[^\\/]*\.dll$",
    re.IGNORECASE,
)
_CUDA_DEST = os.path.join("cuda", "bin", "x64")


def _py_modules_under(pkg: str) -> list[str]:
    """パッケージの下の .py を全部モジュール名にする (__init__.py の無い名前空間のサブパッケージも。
    collect_submodules は pkgutil に頼るのでそれを見落とす: cuda.pathfinder の _dynamic_libs など)。"""
    spec = importlib.util.find_spec(pkg)
    out = []
    for base in list(spec.submodule_search_locations or []) if spec else []:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            rel = os.path.relpath(root, base)
            prefix = pkg if rel == "." else pkg + "." + rel.replace(os.sep, ".")
            out += [prefix if f == "__init__.py" else f"{prefix}.{f[:-3]}" for f in files if f.endswith(".py")]
    return out


def _not_tests(name: str) -> bool:
    # cupy.testing は残す (cupy/__init__ が LazyLoader で find_spec するので、無いと import cupy が失敗する)
    return ".tests" not in name


def _nvrtc_dlls() -> tuple[list[str], str | None]:
    """同梱する NVRTC の DLL と使用許諾のファイル。pip の wheel (nvidia-cuda-nvrtc) を優先し、無ければ CUDA Toolkit。"""
    spec = importlib.util.find_spec("nvidia")
    for base in list(spec.submodule_search_locations) if spec and spec.submodule_search_locations else []:
        dlls = sorted(glob.glob(os.path.join(base, "cu13", "bin", "*", "nvrtc64_*_0.dll")))
        if dlls:
            d = os.path.dirname(dlls[-1])
            lic = sorted(glob.glob(os.path.join(os.path.dirname(base), "nvidia_cuda_nvrtc-*.dist-info", "licenses",
                                                "*.txt")))
            return [dlls[-1]] + sorted(glob.glob(os.path.join(d, "nvrtc-builtins64_*.dll"))), (lic[-1] if lic else None)
    cuda = os.environ.get("CUDA_PATH", "")
    for d in (os.path.join(cuda, "bin", "x64"), os.path.join(cuda, "bin")):
        dlls = sorted(glob.glob(os.path.join(d, "nvrtc64_*_0.dll")))
        if dlls:
            print(f"warning: NVRTC を CUDA Toolkit から同梱します ({d})。uv sync --group dist で wheel を入れてください")
            eula = os.path.join(cuda, "EULA.txt")
            return [dlls[-1]] + sorted(glob.glob(os.path.join(d, "nvrtc-builtins64_*.dll"))), (
                eula if os.path.isfile(eula) else None)
    return [], None


_HAVE_CUPY = importlib.util.find_spec("cupy") is not None
if _HAVE_CUPY and sys.platform == "win32":
    hiddenimports += collect_submodules("cupy", filter=_not_tests)
    hiddenimports += collect_submodules("cupy_backends", filter=_not_tests)
    hiddenimports += collect_submodules("cupyx", filter=_not_tests)
    # CuPy が CUDA のライブラリ・ヘッダを探す。子プロセスで自分を呼ぶ (run_server.py の _run_child_module)
    hiddenimports += _py_modules_under("cuda.pathfinder")
    datas += collect_data_files("cupy", includes=["_core/include/**/*", ".data/*.json"])
    _nvrtc, _license = _nvrtc_dlls()
    if len(_nvrtc) < 2:
        raise SystemExit("NVRTC (nvrtc64_*_0.dll と nvrtc-builtins64_*.dll) が見つかりません。"
                         "uv sync --extra gpu --group dist で nvidia-cuda-nvrtc を入れてください")
    binaries += [(f, _CUDA_DEST) for f in _nvrtc]
    if _license:
        datas.append((_license, "cuda"))  # NVIDIA の使用許諾 (NVRTC は再配布できるライブラリ)

a = Analysis(
    ["run_server.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["pyi_rth_es_sim.py"],
    excludes=[
        # 不要な大物を除外してサイズ削減 (バックエンドは GUI を持たない)
        "tkinter",
        "matplotlib",
        "PIL",
        "IPython",
        "pytest",
    ],
    noarchive=False,
    # es_sim はソースのまま (numba のキャッシュ、トレースバックの行)
    module_collection_mode={"es_sim": "py"},
)
# CUDA Toolkit から拾われた DLL を除く (cuda/bin/x64 に入れた NVRTC だけを残す)
a.binaries = [
    b for b in a.binaries
    if not (_CUDA_DLL.match(os.path.basename(b[0])) and os.path.normpath(os.path.dirname(b[0])) != _CUDA_DEST)
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="picora-2d-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX はアンチウイルス誤検知の一因になるため使わない
    console=True,        # サイドカーはウィンドウ非表示で起動される (ログは親が回収)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="picora-2d-backend",
)

