"""Video encoders. The build steps are written for NVIDIA's NVENC, which encodes a 4K frame in a few milliseconds.
On a machine without it, the same ffmpeg command lines are rewritten for the CPU encoders (x265, x264, SVT-AV1):
slower, the same look.

TVFRAME_ENCODER in config.env picks one: auto (the default: NVENC when it works here, else the CPU), nvenc or cpu.

    encoders.ffmpeg(["ffmpeg", ..., "-c:v", "hevc_nvenc", ...])   the command line for this machine
    python3 server/encoders.py ffmpeg <args>                       the same, from a shell script
    python3 server/encoders.py                                     prints which encoder this machine uses
"""
import os, subprocess, sys
import tvconf

CPU = {"hevc_nvenc": "libx265", "h264_nvenc": "libx264", "av1_nvenc": "libsvtav1"}
# NVENC presets run p1 (fastest) to p7 (best); these are the CPU presets that take about as much care
PRESET = {"libx265": {"p7": "medium", "p6": "fast", "p5": "faster", "p4": "veryfast"},
          "libx264": {"p7": "slow", "p6": "medium", "p5": "fast", "p4": "faster"},
          "libsvtav1": {"p7": "6", "p6": "8", "p5": "9", "p4": "10"}}


def backend():
    """nvenc or cpu. Worked out once, then handed to the steps this one starts through the environment."""
    want = tvconf.get("TVFRAME_ENCODER", "auto").lower()
    if want not in ("nvenc", "cpu"):
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256", "-frames:v", "1",
                                "-c:v", "hevc_nvenc", "-f", "null", "-"], capture_output=True, timeout=60)
            want = "nvenc" if r.returncode == 0 else "cpu"
        except (OSError, subprocess.TimeoutExpired):
            want = "cpu"
        os.environ["TVFRAME_ENCODER"] = want
        tvconf._C["TVFRAME_ENCODER"] = want
    return want


def hwaccel():
    """Decoder flags: CUDA decoding goes with NVENC."""
    return ["-hwaccel", "cuda"] if backend() == "nvenc" else []


def ffmpeg(argv):
    """An ffmpeg command line written for NVENC, as this machine should run it."""
    argv = [str(a) for a in argv]
    if backend() == "nvenc":
        return argv
    codec = next((CPU[argv[i + 1]] for i, a in enumerate(argv[:-1]) if a == "-c:v" and argv[i + 1] in CPU), None)
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        b = argv[i + 1] if i + 1 < len(argv) else None
        if a == "-hwaccel" and b == "cuda":
            i += 2
            continue
        if codec and a in ("-tune", "-rc"):
            i += 2
            continue
        if codec and a == "-b:v":                          # quality-targeted below; -maxrate still caps it
            i += 2
            continue
        if codec and a == "-bf" and codec == "libsvtav1":
            i += 2
            continue
        if a == "-c:v" and b in CPU:
            out += ["-c:v", codec]
        elif codec and a == "-preset":
            out += ["-preset", PRESET[codec].get(b, "medium" if codec != "libsvtav1" else "8")]
        elif codec and a in ("-cq", "-qp"):
            q = float(b)
            if codec == "libsvtav1":                       # av1_nvenc's qp runs 0-255, SVT-AV1's crf 0-63
                q = q * 63 / 255
            out += ["-crf", str(max(0, min(51 if codec != "libsvtav1" else 63, round(q))))]
        elif a in ("-vf", "-filter_complex") and b is not None:
            out += [a, b.replace("format=p010le", "format=yuv420p10le")]   # x265 takes planar 10-bit
        else:
            out.append(a)
            i += 1
            continue
        i += 2
    if codec == "libx265":
        at = out.index("-c:v") + 2
        out[at:at] = ["-x265-params", "log-level=error"]
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ffmpeg":
        cmd = ffmpeg(sys.argv[1:])
        os.execvp(cmd[0], cmd)
    print(backend())
