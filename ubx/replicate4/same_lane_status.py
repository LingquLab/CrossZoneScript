"""Read the single-route-QP baseline result or saved failure evidence."""
from disjoint_routes_status import main
from retest_same_lane import TASK, REVISION, WORK

if __name__ == "__main__":
    main(task=TASK, revision=REVISION, work=WORK, mode="same_lane")
