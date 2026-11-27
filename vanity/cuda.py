"""CUDA Driver API bindings and the NVIDIA search backend."""
import ctypes as c
from gzip import decompress
from pathlib import Path
import sys

from .packing import GPUJob, THREADS, PER_THREAD

U32, U64, PTR, INT = c.c_uint32, c.c_uint64, c.c_void_p, c.c_int


class Driver:
    def __init__(self):
        try:
            self.library = c.WinDLL("nvcuda.dll") if sys.platform == "win32" else c.CDLL("libcuda.so.1")
        except OSError as error:
            raise RuntimeError("NVIDIA CUDA driver not found. Check the installed NVIDIA display driver.") from error
        signatures = {
            "cuInit": [U32], "cuDeviceGetCount": [c.POINTER(INT)],
            "cuDeviceGet": [c.POINTER(INT), INT], "cuDeviceGetName": [PTR, INT, INT],
            "cuDeviceGetAttribute": [c.POINTER(INT), INT, INT],
            "cuCtxCreate_v2": [c.POINTER(PTR), U32, INT], "cuCtxDestroy_v2": [PTR],
            "cuModuleLoadData": [c.POINTER(PTR), PTR], "cuModuleGetFunction": [c.POINTER(PTR), PTR, c.c_char_p],
            "cuMemAlloc_v2": [c.POINTER(U64), c.c_size_t], "cuMemcpyHtoD_v2": [U64, PTR, c.c_size_t],
            "cuMemcpyDtoH_v2": [PTR, U64, c.c_size_t], "cuMemsetD32_v2": [U64, U32, c.c_size_t],
            "cuLaunchKernel": [PTR, U32, U32, U32, U32, U32, U32, U32, PTR, c.POINTER(PTR), c.POINTER(PTR)],
            "cuGetErrorName": [INT, c.POINTER(c.c_char_p)],
        }
        for name, args in signatures.items():
            fn = getattr(self.library, name)
            fn.argtypes = args
            fn.restype = INT
        self.call("cuInit", 0)

    def call(self, name, *args):
        code = getattr(self.library, name)(*args)
        if code:
            label = c.c_char_p()
            self.library.cuGetErrorName(code, c.byref(label))
            description = label.value.decode() if label.value else str(code)
            raise RuntimeError(f"{name} failed: {description}.")

    def device(self, index):
        count = INT()
        self.call("cuDeviceGetCount", c.byref(count))
        if not 0 <= index < count.value:
            raise RuntimeError(f"CUDA device {index} unavailable ({count.value} detected)")
        device = INT()
        self.call("cuDeviceGet", c.byref(device), index)
        name = c.create_string_buffer(128)
        self.call("cuDeviceGetName", name, len(name), device)
        return device, name.value.decode(errors="replace")

    def load(self, compressed_ptx):
        module = PTR()
        image = c.create_string_buffer(decompress(compressed_ptx.read_bytes()))
        self.call("cuModuleLoadData", c.byref(module), image)
        return module

    def function(self, module, name):
        result = PTR()
        self.call("cuModuleGetFunction", c.byref(result), module, name.encode())
        return result

    def buffer(self, size):
        address = U64()
        self.call("cuMemAlloc_v2", c.byref(address), size)
        return address

    def upload(self, address, data):
        buffer = c.create_string_buffer(data)
        self.call("cuMemcpyHtoD_v2", address, buffer, len(data))

    def launch(self, function, blocks, threads, *values):
        args = (PTR * len(values))(*(c.addressof(value) for value in values))
        self.call("cuLaunchKernel", function, blocks, 1, 1, threads, 1, 1, 0, None, args, None)


class CUDA:
    maximum_batch = 2 ** 26

    def __init__(self, index):
        self.driver = Driver()
        self.device, name = self.driver.device(index)
        self.name = f"CUDA · {name}"
        self.ctx = PTR()
        self.ptx = Path(__file__).with_name("search.ptx.gz")

    def configure(self, base, tag, patterns):
        self.close()
        self.job = GPUJob(base, tag, patterns)
        driver = self.driver
        driver.call("cuCtxCreate_v2", c.byref(self.ctx), 0, self.device)
        try:
            module = driver.load(self.ptx)
            self.scan = driver.function(module, "scan")
            self.inputs = []
            for data in (self.job.data, self.job.buckets, self.job.patterns):
                buffer = driver.buffer(len(data))
                driver.upload(buffer, data)
                self.inputs.append(buffer)
            self.enabled = driver.buffer(self.job.groups * 4)
            self.output = driver.buffer(self.job.output_size)
        except BaseException:
            self.close()
            raise

    def batch(self, start, size, quotas):
        driver = self.driver
        driver.upload(self.enabled, self.job.active(quotas))
        driver.call("cuMemsetD32_v2", self.output, 0, self.job.output_size // 4)
        blocks = (size + THREADS * PER_THREAD - 1) // (THREADS * PER_THREAD)
        driver.launch(self.scan, blocks, THREADS, *self.inputs, self.enabled, self.output,
                      U64(start), U32(size), U32(self.job.groups))
        result = c.create_string_buffer(self.job.output_size)
        driver.call("cuMemcpyDtoH_v2", result, self.output, len(result))
        return self.job.results(result.raw, size, quotas)

    def close(self):
        if self.ctx:
            self.driver.call("cuCtxDestroy_v2", self.ctx)
            self.ctx = PTR()
