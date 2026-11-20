"""Run the prepared 64P 4 MiB points with published passing references."""

from run_prepared_64p import run_prepared_points


if __name__ == '__main__':
    run_prepared_points(16384, {
        'urma_lr8': 2303.000,
        'urma_lr8_p16_s16_local7': 2004.580,
    }, simulation_timeout=7200)
