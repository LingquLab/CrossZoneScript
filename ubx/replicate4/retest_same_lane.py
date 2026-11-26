"""Benchmark the EP32 BS32 single-route-QP baseline."""
from retest_disjoint_routes import ROOT, main

TASK = "replicate-baseline-bs32"
REVISION = "93a51d4f2e26d90410647b4745012edf65ccd0a9"
WORK = ROOT + "-same-lane-baseline"

if __name__ == "__main__":
    main(revision=REVISION, work=WORK, mode="same_lane", ports=(29500, 26500, 31000))
