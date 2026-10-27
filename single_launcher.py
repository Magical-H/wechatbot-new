"""PyInstaller onefile 启动入口。"""
import os
import runpy
import sys
import importlib.util
import shutil


APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def prepare_user_files():
    """首次启动时把可编辑配置和素材释放到 EXE 同目录。"""
    resource_dir = getattr(sys, "_MEIPASS", APP_DIR)
    for name in ("config.py", "prompts", "emojis", "templates", "Demo_Image"):
        source = os.path.join(resource_dir, "defaults", name) if name == "config.py" else os.path.join(resource_dir, name)
        destination = os.path.join(APP_DIR, name)
        if os.path.exists(destination) or not os.path.exists(source):
            continue
        if os.path.isdir(source):
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)


def main():
    if "--self-test" in sys.argv:
        if getattr(sys, "frozen", False):
            bundle = os.path.join(sys._MEIPASS, "defaults", "config.py")
            required = [bundle] + [os.path.join(sys._MEIPASS, name) for name in ("prompts", "emojis", "templates", "Demo_Image")]
            if not all(os.path.exists(path) for path in required):
                raise RuntimeError("EXE 内的默认配置或素材不完整。")
        import config_editor  # noqa: F401
        import wxbot  # noqa: F401
        import wechatauto  # noqa: F401
        if importlib.util.find_spec("bot") is None:
            raise RuntimeError("机器人入口未包含在 EXE 中。")
        print("自检通过：EXE 启动、配置界面、微信适配层及机器人入口均可加载。")
        return 0
    prepare_user_files()
    if "--bot" in sys.argv:
        import bot
        bot.main()
        return 0
    runpy.run_module("config_editor", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
