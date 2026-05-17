from .queue_bottleneck import QueueBottleneck
from .scale_out_lag import ScaleOutLag
from .cpu_bottleneck import CpuBottleneck
from .hpa_limitation import HpaLimitation
from ._gpu_compute import GpuCompute
from ._gpu_memory import GpuMemory
from ._gpu_scheduling import GpuScheduling

ALL_RULES = [
    QueueBottleneck,
    ScaleOutLag,
    CpuBottleneck,
    HpaLimitation,
    GpuCompute,
    GpuMemory,
    GpuScheduling,
]
