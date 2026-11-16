"""hemo_frames_custom.py -- 稳健版 HEMO 帧导出（仅 25 帧 WSS，不做 snapshot）。

用法: python hemo_frames_custom.py <tag> [cores]
- 显式 read-data，保证从周期末数据继续（不被 case-only 读取）
- 仅导出缺失帧；journal 结尾 exit/yes/yes；Fluent 超时 7200 s
- 帧已存在 >50KB 则跳过，支持断点续跑
"""
import json
import os
import subprocess
import sys

WS = os.environ.get("CFD_WORKSPACE", os.getcwd())
WK = os.environ.get("CFD_WK", os.path.join(WS, "work", "wkcase"))
GEOM = os.environ.get("CFD_GEOM", os.path.join(WS, "meshes"))
OUT = os.environ.get("CFD_HEMO", os.path.join(WS, "work", "hemo"))
FLUENT = os.environ.get("CFD_FLUENT", r"D:\ANSYS2024R2\ANSYS Inc\v242\fluent\ntbin\win64\fluent.exe")
N = 25
STEPS = 10


def walls_of(tag):
    p = os.path.join(GEOM, "walls_hemo.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        if tag in d:
            return d[tag]
    import h5py
    mp = os.path.join(GEOM, "mesh_%s_bc.msh.h5" % tag)
    f = h5py.File(mp, "r")
    g = f["meshes/1/faces/zoneTopology"]
    names = g["name"][0].decode("latin-1").split(";")
    types = g["zoneType"][:]
    walls = [n for ty, n in zip(types, names) if int(ty) == 3]
    f.close()
    return walls


def main():
    tag = sys.argv[1]
    cores = sys.argv[2] if len(sys.argv) > 2 else "4"
    d = os.path.join(OUT, tag)
    frames = os.path.join(d, "frames")
    os.makedirs(frames, exist_ok=True)
    walls = walls_of(tag)
    base = WK.replace("\\", "/")
    L = ['/file/read-case-data "%s/wk_%s_out.cas.gz"' % (base, tag),
         '/file/read-data "%s/wk_%s_out.dat.gz"' % (base, tag),
         '/file/auto-save/data-frequency', '0', 'q']
    missing = 0
    for i in range(N):
        out = os.path.join(frames, "%04d.txt" % i).replace("\\", "/")
        if os.path.exists(out) and os.path.getsize(out) > 50000:
            continue
        missing += 1
        L += ['/solve/dual-time-iterate', str(STEPS), '20',
              '/file/export/ascii', out,
              " ".join('"%s"' % w for w in walls),
              '()', 'no', 'x-wall-shear', 'y-wall-shear', 'z-wall-shear', '()', 'no']
    if missing == 0:
        print("frames already complete", tag, flush=True)
        return
    L += ['exit', 'yes', 'yes']
    jp = os.path.join(d, "frames_custom.jou")
    lp = os.path.join(d, "frames_custom.log")
    open(jp, "w", encoding="ascii").write("\n".join(L) + "\n")
    cmd = [FLUENT, "3ddp", "-g", "-t%s" % cores, "-i", jp]
    with open(lp, "w") as fh:
        try:
            subprocess.run(cmd, cwd=WK, stdout=fh, stderr=subprocess.STDOUT, timeout=7200)
        except subprocess.TimeoutExpired:
            pass
    n = len([f for f in os.listdir(frames) if f.endswith(".txt")])
    print("tag", tag, "missing_start", missing, "frames_now", n, flush=True)


if __name__ == "__main__":
    main()
