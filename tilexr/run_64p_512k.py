"""Run the four prepared 64P suites at 512 KiB per rank."""

from prepare_64p import KERNELS
from run_prepared_64p import run_prepared_points


if __name__ == '__main__':
    run_prepared_points(2048, dict(zip(KERNELS, (357.319, 348.828, 313.188, 318.560), strict=True)),
                        simulation_timeout=3600)
