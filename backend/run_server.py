"""PICORA-2D のローカル計算サーバーを起動する。"""

from __future__ import annotations

import argparse
import multiprocessing
import sys

import uvicorn


def _run_child_module():
    # 凍結 exe で CuPy の CUDA ヘッダ探索が要求する子プロセスに対応する。
    if len(sys.argv) >= 3 and sys.argv[1] == "-m" and sys.argv[2].startswith("cuda.pathfinder."):
        import runpy

        module = sys.argv[2]
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="PICORA-2D calculation server")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("selftest", add_help=False)
    args, rest = parser.parse_known_args()
    if args.command == "selftest":
        from es_sim.selftest import main as selftest_main

        raise SystemExit(selftest_main(rest))
    if rest:
        parser.error(f"不明な引数: {' '.join(rest)}")
    from es_sim.device import cuda_status

    ok, info = cuda_status()
    print(f"PICORA-2D: GPU {'あり' if ok else 'なし'} ({info})", flush=True)
    uvicorn.run("es_sim.server:app", host=args.host, port=args.port, workers=1, reload=False)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _run_child_module()
    main()
