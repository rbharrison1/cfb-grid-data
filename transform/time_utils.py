"""Time-related utility functions.

Ported unchanged from cfb-grid-python/mern/python/v2/utils/time_utils.py.
"""

from datetime import datetime, time, timedelta

def get_time_window(start_time: str) -> int:
    """
    Get the time window number based on the start time.

    Args:
        start_time (str): Time in format "HH:MM"

    Returns:
        int: Time window number (1-38) or None if outside valid windows
    """
    hour = int(start_time[0:2])
    minute = int(start_time[3:5])

    # Convert hour to 0-23 range if it's 24+
    if hour >= 24:
        hour = hour - 24

    start_time = time(hour=hour, minute=minute)

    # Define time windows with broader coverage starting at 6:00 AM
    time_windows = [
        (time(6, 0), time(6, 30), 1),      # Starting at 6:00 AM
        (time(6, 30), time(7, 0), 2),
        (time(7, 0), time(7, 30), 3),
        (time(7, 30), time(8, 0), 4),
        (time(8, 0), time(8, 30), 5),
        (time(8, 30), time(9, 0), 6),
        (time(9, 0), time(9, 30), 7),
        (time(9, 30), time(10, 0), 8),
        (time(10, 0), time(10, 30), 9),
        (time(10, 30), time(11, 0), 10),
        (time(11, 0), time(11, 30), 11),
        (time(11, 30), time(12, 0), 12),
        (time(12, 0), time(12, 30), 13),
        (time(12, 30), time(13, 0), 14),
        (time(13, 0), time(13, 30), 15),
        (time(13, 30), time(14, 0), 16),
        (time(14, 0), time(14, 30), 17),
        (time(14, 30), time(15, 0), 18),
        (time(15, 0), time(15, 30), 19),
        (time(15, 30), time(16, 0), 20),
        (time(16, 0), time(16, 30), 21),
        (time(16, 30), time(17, 0), 22),
        (time(17, 0), time(17, 30), 23),
        (time(17, 30), time(18, 0), 24),
        (time(18, 0), time(18, 30), 25),
        (time(18, 30), time(19, 0), 26),
        (time(19, 0), time(19, 30), 27),
        (time(19, 30), time(20, 0), 28),
        (time(20, 0), time(20, 30), 29),
        (time(20, 30), time(21, 0), 30),
        (time(21, 0), time(21, 30), 31),
        (time(21, 30), time(22, 0), 32),
        (time(22, 0), time(22, 30), 33),
        (time(22, 30), time(23, 0), 34),
        (time(23, 0), time(23, 30), 35),
        (time(23, 30), time(23, 59), 36),
        (time(0, 0), time(0, 30), 37),
        (time(0, 30), time(1, 0), 38)
    ]

    for start, end, window in time_windows:
        if start <= start_time < end:
            return window

    # If no window is found, use a fallback based on 30-minute blocks starting from 6:00 AM
    total_minutes = hour * 60 + minute
    base_minutes = 6 * 60  # 6:00 AM in minutes

    # Handle times before 6:00 AM (consider them as next day continuation)
    if total_minutes < base_minutes:
        # For times from 00:00 to 05:59, add 24 hours worth of minutes
        total_minutes += 24 * 60

    window_number = ((total_minutes - base_minutes) // 30) + 1
    return max(1, window_number)  # Ensure we don't return negative or zero

def adjust_datetime_for_timezone(date_value, timezone: str) -> datetime:
    """
    Adjust datetime based on timezone.

    Args:
        date_value: ISO format datetime string, OR a datetime/pandas Timestamp --
            BigQuery's schema autodetect infers a TIMESTAMP column for
            CFBD's ISO-8601 startDate field, so to_dataframe() hands back
            an already-parsed (UTC-aware) Timestamp rather than a string.
        timezone (str): Single letter timezone code ('E', 'C', 'M', 'P', 'A', 'H')

    Returns:
        datetime: Adjusted datetime
    """
    timezone_offsets = {
        'E': 4,  # Eastern
        'C': 5,  # Central
        'M': 6,  # Mountain
        'P': 7,  # Pacific
        'A': 8,  # Alaska
        'H': 10  # Hawaii
    }

    if isinstance(date_value, str):
        base_dt = datetime.strptime(date_value[:16], '%Y-%m-%dT%H:%M')
    else:
        base_dt = date_value.to_pydatetime() if hasattr(date_value, 'to_pydatetime') else date_value
        if base_dt.tzinfo is not None:
            base_dt = base_dt.replace(tzinfo=None)

    return base_dt - timedelta(hours=timezone_offsets.get(timezone, 4))
