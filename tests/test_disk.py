import pytest

from src.disk import format_bytes
from src.disk import get_disk_usage, get_free_space


@pytest.mark.parametrize(
    "n_bytes, expected",
    [
        (0, "0 MB"),
        (500 * 1024 * 1024, "500 MB"),           # 500 MiB
        (999 * 1024 * 1024, "999 MB"),           # just under 1 GiB
        (1024 * 1024 * 1024, "1 GB"),            # exactly 1 GiB
        (55 * 1024 * 1024 * 1024, "55 GB"),      # 55 GiB
        (1536 * 1024 * 1024, "2 GB"),            # rounding up to nearest GB
    ],
)
def test_format_bytes(n_bytes, expected):
    assert format_bytes(n_bytes) == expected


def test_get_free_space_returns_positive_int():
    free = get_free_space("/")
    assert isinstance(free, int)
    assert free > 0


def test_get_disk_usage_returns_total_used_free():
    total, used, free = get_disk_usage("/")
    assert isinstance(total, int) and total > 0
    assert isinstance(used, int) and used >= 0
    assert isinstance(free, int) and free >= 0
    # Used + free should be close to total (filesystems reserve some space).
    assert used + free <= total
    assert used + free >= total * 0.8
