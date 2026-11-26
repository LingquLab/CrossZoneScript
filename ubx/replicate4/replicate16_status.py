"""Report the replicate16 benchmark state or recover its saved results."""
from disjoint_routes_status import main
from retest_replicate16 import REVISION, WORK

if __name__ == "__main__":
    main(task="replicate16-disjoint-bs32-retest", revision=REVISION,
         work=WORK, mode="replicate16")
