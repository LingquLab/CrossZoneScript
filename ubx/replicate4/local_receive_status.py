"""Read the local-receive benchmark status and bounded diagnostics."""
from disjoint_routes_status import main
from retest_local_receive import REVISION, TASK, WORK

if __name__ == "__main__":
    main(task=TASK, revision=REVISION, work=WORK)
