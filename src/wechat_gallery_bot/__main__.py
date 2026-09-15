import argparse
import logging
import sqlite3
from pathlib import Path

from .adapters.base import AdapterError
from .config import Config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="天意Bot：微信群本地图库")
    parser.add_argument("mode", choices=("demo", "check", "run"), nargs="?", default="demo")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--live", action="store_true", help="明确启动真实群监听和自动回复")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        if args.mode == "demo":
            from .demo import run_demo
            run_demo()
            return 0
        config = Config.load(args.env)
        from .adapters.wxauto_adapter import check_backend
        if args.mode == "check":
            print(f"配置有效：{len(config.groups)} 个群；超时 {config.pending_seconds:g} 秒。")
            print(f"免费后端来源校验通过：wxauto4 {check_backend()}。未连接微信。")
            print("目标客户端兼容性需另行实测；固定免费上游声明支持4.0.5。")
            return 0
        if not args.live:
            parser.error("真实启动请显式使用 run --live；离线演示使用 demo。")
        raise ValueError("直接运行入口已停用以保护主号。请在虚拟机启动服务，通过主机GUI核对小号后启动。")
    except (AdapterError, ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        # Adapter/config messages are controlled; avoid OS paths and account details.
        print(str(exc) if isinstance(exc, (AdapterError, ValueError, RuntimeError)) else "本地文件操作失败，请检查目录权限。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
