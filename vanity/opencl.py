"""OpenCL GPU backend for AMD, Intel, and NVIDIA implementations."""
from .packing import GPUJob, PER_THREAD, SOURCE, THREADS


class OpenCL:
    maximum_batch = 2 ** 25

    def __init__(self, index, vendor=None, *, allow_cpu=False):
        import numpy as np
        import pyopencl as cl

        self.cl, self.np = cl, np
        kind = cl.device_type.ALL if allow_cpu else cl.device_type.GPU
        devices = [device for platform in cl.get_platforms()
                   for device in platform.get_devices(kind) if device.available and device.compiler_available]
        if vendor:
            markers = {"amd": ("amd", "advanced micro devices"), "intel": ("intel",)}[vendor]
            devices = [device for device in devices if any(mark in device.vendor.lower() for mark in markers)]
        if not 0 <= index < len(devices):
            raise RuntimeError(f"OpenCL device {index} unavailable ({len(devices)} compatible devices detected)")
        self.device = devices[index]
        if not self.device.endian_little:
            raise RuntimeError("OpenCL supports little-endian devices in this build")
        self.name = f"OpenCL · {self.device.name.strip()}"
        self.context = cl.Context([self.device])
        self.queue = cl.CommandQueue(self.context)
        self.program = cl.Program(self.context, SOURCE.read_text(encoding="ascii")).build(options=["-cl-std=CL1.2"])
        self.kernel = cl.Kernel(self.program, "scan")
        self.threads = min(THREADS, self.kernel.get_work_group_info(cl.kernel_work_group_info.WORK_GROUP_SIZE, self.device))
        self.inputs = ()
        self.enabled = self.output = None

    def configure(self, base, tag, patterns):
        self.close()
        self.job = GPUJob(base, tag, patterns)
        cl = self.cl
        flags = cl.mem_flags
        self.inputs = tuple(cl.Buffer(self.context, flags.READ_ONLY | flags.COPY_HOST_PTR, hostbuf=data)
                            for data in (self.job.data, self.job.buckets, self.job.patterns))
        self.enabled = cl.Buffer(self.context, flags.READ_ONLY, self.job.groups * 4)
        self.output = cl.Buffer(self.context, flags.READ_WRITE, self.job.output_size)

    def batch(self, start, size, quotas):
        cl, np = self.cl, self.np
        active = self.job.active(quotas)
        cl.enqueue_copy(self.queue, self.enabled, active)
        cl.enqueue_fill_buffer(self.queue, self.output, np.uint32(0), 0, self.job.output_size)
        step = self.threads * PER_THREAD
        threads = (size + step - 1) // step * self.threads
        self.kernel(self.queue, (threads,), (self.threads,), *self.inputs, self.enabled, self.output,
                    np.uint64(start), np.uint32(size), np.uint32(self.job.groups))
        result = bytearray(self.job.output_size)
        cl.enqueue_copy(self.queue, result, self.output, is_blocking=True)
        return self.job.results(result, size, quotas)

    def close(self):
        self.inputs = ()
        self.enabled = self.output = None
