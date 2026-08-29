"""Prevents Windows idle-triggered sleep/Modern Standby for the duration this
process runs. NOT a repo module — a standalone companion process, launched
alongside a long unattended training run and killed when it ends.

Why it exists (2026-08-28): STANDBYIDLE/HIBERNATEIDLE are both 0 (never) in
the active power scheme, yet Windows Event Viewer recorded Kernel-Power
Id=506 "entering Modern Standby, Reason: Idle Timeout" during an evening of
unattended D1 training. This laptop uses Modern Standby (S0 low power idle),
which HP/Windows can drive into idle-suspend on its own idle heuristic
independent of the classic SUB_SLEEP timers, so holding the machine awake
explicitly is still worth doing for a genuinely-solo overnight run.
`powercfg /requestsoverride` is the sanctioned fix but requires an elevated
(admin) shell, which was not available here.

CORRECTION (2026-08-29 audit): an earlier version of this docstring asserted
those 506 events "killed two training resumes (leg 2 at ~17:00-17:05, leg 3
at ~17:10:56) via a dead TraCI socket." The repo record refutes that
attribution. Two SUMO sweeps (_sweeps/detstoch.log, _sweeps/emerg.log) ran
to completion straight through that window; a machine actually in Modern
Standby could not have let them finish. The D1 legs that died there almost
certainly died from concurrent multi-SUMO TraCI-port contention (BUILD_LOG's
documented cause for the FatalTraCIError), not from sleep — and the "leg 2 /
leg 3" numbering does not map onto any log/monitor/tb artifact in the repo.
The 506 events may be real; they were not what killed training on 08-28.
This tool is kept because idle-suspend on a genuinely-solo long run is a
real risk worth removing — just not the one that bit that evening.

SetThreadExecutionState is the same Win32 API `powercfg /requestsoverride`
ultimately arms on the caller's behalf, and — unlike requestsoverride — a
process may call it for ITSELF without admin rights. Holding
ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED keeps the system
out of idle sleep/standby for as long as this process is alive; killing it
(or the training process alongside which it should be started/stopped)
releases the request automatically.
"""

import ctypes
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040

FLAGS = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED

if __name__ == "__main__":
    result = ctypes.windll.kernel32.SetThreadExecutionState(FLAGS)
    if result == 0:
        raise SystemExit("SetThreadExecutionState failed (returned 0)")
    print(f"keep_awake: ES_SYSTEM_REQUIRED|ES_AWAYMODE_REQUIRED armed (result=0x{result:x}). "
          "Sleeping until killed.")
    try:
        while True:
            time.sleep(60)
            # Re-assert periodically — ES_CONTINUOUS should persist on its own,
            # but re-arming is cheap insurance against any transient reset.
            ctypes.windll.kernel32.SetThreadExecutionState(FLAGS)
    except KeyboardInterrupt:
        pass
