"""Conservative timestamp checks for the synchronized split-host ROS graph."""

CLOCK_UNCERTAINTY = 0.025


def remote_age(local_time, remote_stamp):
    """Bound the age above using the measured clock skew budget."""
    age = local_time-remote_stamp
    if age < -CLOCK_UNCERTAINTY:
        return float('inf')
    return age+CLOCK_UNCERTAINTY
