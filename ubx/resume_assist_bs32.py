"""Resume the final profiling case after the previous recovery service stopped."""

import sys

from retry_remaining import run_pair, status
from retry_transport import retry


if __name__ == "__main__":
    coordinator, peer = sys.argv[1:]
    case = "tail-reviewed-dispatch-bs32-on"
    status("active", case)
    retry(coordinator, peer, case, pair=run_pair, transport_only=False, allow_incomplete=True)
    status("completed")
