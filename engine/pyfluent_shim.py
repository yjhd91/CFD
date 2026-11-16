"""pyfluent_shim.py — 修复 PyFluent 0.20 (ANSYS 2024 R2) 在 Windows 上启动 Fluent 的 PermissionError。

实测根因（两个叠加）：
1) ansys.fluent.core.launcher.watchdog 用字符串命令 'start "" "fluent.exe" ...' 调
   subprocess.Popen(...) 却没给 shell=True；"start" 是 cmd 内建命令 → CreateProcess 失败。
2) watchdog 的 creationflags 含 CREATE_BREAKAWAY_FROM_JOB；如果宿主进程位于不允许
   breakaway 的 Windows Job 对象中（DSH/代理环境常见），CreateProcess 直接返回
   [WinError 5] 拒绝访问。

修复：只替换 watchdog 模块内部的 subprocess 引用（代理对象），
   - 字符串命令以 'start ' 开头时加 shell=True；
   - 从 creationflags 中去掉 CREATE_BREAKAWAY_FROM_JOB。
不替换全局 subprocess.Popen（全局替换会破坏 asyncio.windows_utils.Popen 的子类化）。

用法：在 import ansys.fluent.core 之前：
    import pyfluent_shim
    import ansys.fluent.core as pf
"""
import subprocess as _sp

_PATCHED = False


def _patch_watchdog():
    global _PATCHED
    if _PATCHED:
        return
    from ansys.fluent.core.launcher import watchdog as _wd

    _BREAKAWAY = getattr(_sp, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)

    class _SubprocessProxy:
        def __getattr__(self, name):
            return getattr(_sp, name)

        @staticmethod
        def Popen(cmd, *args, **kwargs):
            if isinstance(cmd, str) and cmd.lstrip().lower().startswith("start "):
                kwargs.setdefault("shell", True)
            flags = kwargs.get("creationflags", 0)
            if flags & _BREAKAWAY:
                kwargs["creationflags"] = flags & ~_BREAKAWAY
            return _sp.Popen(cmd, *args, **kwargs)

    _wd.subprocess = _SubprocessProxy()
    _PATCHED = True


_patch_watchdog()
