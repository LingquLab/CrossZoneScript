"""Build the replicate16 benchmark revision and run full EP32 BS32."""
from retest_disjoint_routes import ROOT, main

REVISION = "f52edb22bbbd3a900e9c875ee56b248512995ed4"
WORK = ROOT + "-replicate16"

if __name__ == "__main__":
    main(revision=REVISION, work=WORK, mode="replicate16", ports=(28000, 30000, 26500))
