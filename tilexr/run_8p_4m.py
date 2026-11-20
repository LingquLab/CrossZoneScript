"""Run the four 8P 4MiB/rank points using already qualified profiled objects."""
from run_profiled_points import run_points

run_points([('urma_lr8', 8, 131072, 3145.200), ('urma_cw', 8, 131072, 3156.860),
            ('urma_lr8_p16_s16_local7', 8, 131072, 3158.770),
            ('urma_lr8_p16_s8_local7', 8, 131072, 3146.110)],
           campaign='profiled-8p-4m-1', simulation_timeout=3600)
