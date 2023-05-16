import pytest

from ...tests import IOC
from ..epid import EpidRecord
from ..epid import Fb_EpidDatabase
from ..epid import Fb_EpidDatabaseHeaterSimulator


@pytest.mark.parametrize(
    "support, ignore",
    [
        [EpidRecord, None],
        [Fb_EpidDatabase, None],
        [Fb_EpidDatabaseHeaterSimulator, None],
    ],
)
def test_connection(support, ignore):
    """Connection test."""
    epid = support(f"{IOC}epid1", name="epid")
    epid.wait_for_connection()

    assert epid.connected


@pytest.mark.parametrize(
    "method, scan, Kp, Ki, hi, T_noise",
    [
        ["reset", 0, 0, 0, 1.0, 0.1],
        ["setup", 8, 4e-4, 0.5, 1.0, 0.1],
    ],
)
def test_sim_heater(method, scan, Kp, Ki, hi, T_noise):
    epid = Fb_EpidDatabaseHeaterSimulator(f"{IOC}epid1", name="epid")
    epid.wait_for_connection()

    getattr(epid, method)()
    assert epid.scanning_rate.get() == scan
    assert epid.proportional_gain.get() == Kp
    assert epid.integral_gain.get() == Ki
    assert epid.high_limit.get() == hi
    assert epid.sim_calc.channels.H.input_value.get() == T_noise
