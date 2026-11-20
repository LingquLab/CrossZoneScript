"""Reproduce the eight 16P and 32P points at 4 MiB per rank."""

from run_profiled_points import run_points


if __name__ == '__main__':
    run_points([
        ('urma_lr8', 16, 65536, 1951.980),
        ('urma_cw', 16, 65536, 1955.580),
        ('urma_lr8_p16_s16_local7', 16, 65536, 1956.800),
        ('urma_lr8_p16_s8_local7', 16, 65536, 1951.200),
        ('urma_lr8', 32, 32768, 2238.580),
        ('urma_cw', 32, 32768, 2015.430),
        ('urma_lr8_p16_s16_local7', 32, 32768, 1954.930),
        ('urma_lr8_p16_s8_local7', 32, 32768, 1950.310),
    ], campaign='profiled-16p-32p-4m-1', simulation_timeout=3600)
