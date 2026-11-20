"""Reproduce the eight 16P and 32P points at 512 KiB per rank."""

from run_profiled_points import run_points


if __name__ == '__main__':
    run_points([
        ('urma_lr8', 16, 8192, 265.615),
        ('urma_cw', 16, 8192, 266.957),
        ('urma_lr8_p16_s16_local7', 16, 8192, 268.563),
        ('urma_lr8_p16_s8_local7', 16, 8192, 266.461),
        ('urma_lr8', 32, 4096, 316.361),
        ('urma_cw', 32, 4096, 290.319),
        ('urma_lr8_p16_s16_local7', 32, 4096, 280.495),
        ('urma_lr8_p16_s8_local7', 32, 4096, 278.318),
    ], campaign='profiled-16p-32p-512k-1', simulation_timeout=1800)
