"""Tests for the progress line.

It writes carriage returns and erase sequences, so it must be silent whenever
stdout is not a terminal, or piped reports would be corrupted.
"""

import io
import time

from netriage.util import CookTimer


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_draws_a_live_counter_on_a_tty():
    stream = FakeTTY()
    timer = CookTimer(stream=stream, enabled=True)
    timer.start()
    time.sleep(1.2)
    timer.stop()

    out = stream.getvalue()
    assert "cooking" in out
    assert out.startswith("\r"), "must return to column 0"
    assert "\033[K" in out, "must erase the line it wrote"


def test_first_second_has_no_zero_counter():
    stream = FakeTTY()
    timer = CookTimer(stream=stream, enabled=True)
    timer.start()
    time.sleep(0.3)
    timer.stop()

    assert "cooking for 0s" not in stream.getvalue()


def test_stop_erases_the_line():
    stream = FakeTTY()
    timer = CookTimer(stream=stream, enabled=True)
    timer.start()
    time.sleep(0.3)
    timer.stop()

    assert stream.getvalue().endswith("\r\033[K")


def test_silent_when_not_a_tty():
    stream = io.StringIO()  # isatty() is False
    timer = CookTimer(stream=stream, enabled=True)
    timer.start()
    time.sleep(0.3)
    timer.stop()

    assert stream.getvalue() == ""


def test_silent_when_disabled():
    stream = FakeTTY()
    timer = CookTimer(stream=stream, enabled=False)
    timer.start()
    time.sleep(0.2)
    timer.stop()

    assert stream.getvalue() == ""


def test_records_elapsed_seconds():
    stream = FakeTTY()
    timer = CookTimer(stream=stream, enabled=True)
    timer.start()
    time.sleep(1.1)
    timer.stop()

    assert timer.seconds >= 1


def test_context_manager_stops_the_thread():
    stream = FakeTTY()
    with CookTimer(stream=stream, enabled=True) as timer:
        time.sleep(0.2)
    assert timer.seconds >= 0
    assert stream.getvalue().endswith("\r\033[K")


def test_set_label_switches_the_text():
    stream = FakeTTY()
    timer = CookTimer(stream=stream, enabled=True)
    timer.start()
    time.sleep(0.5)          # let the loop draw at least one "cooking" frame
    timer.set_label("scanning")
    time.sleep(0.3)
    timer.stop()

    out = stream.getvalue()
    assert "cooking" in out
    assert "scanning" in out
    assert out.index("scanning") > out.index("cooking")


def test_counter_restarts_per_phase():
    """'measuring speed for 2s' must describe the phase, not the whole run."""
    stream = FakeTTY()
    timer = CookTimer(stream=stream, enabled=True)
    timer.start()
    time.sleep(1.1)
    assert timer.seconds >= 1

    timer.set_label("scanning")
    assert timer.phase_seconds == 0, "the live counter restarts per phase"
    assert timer.seconds >= 1, "the total keeps running"

    time.sleep(0.2)
    timer.stop()


def test_set_label_before_start_is_safe():
    timer = CookTimer(stream=FakeTTY(), enabled=True)
    timer.set_label("scanning")
    assert timer.label == "scanning"


def test_disable_colour_reaches_importing_modules():
    """The modules import these by value, so disabling must update them too."""
    import netriage.cli as cli
    import netriage.util as util

    try:
        util.enable_color()
        assert cli.BOLD != "", "enable_color must reach cli"
        util.disable_color()
        assert cli.BOLD == "", "disable_color must reach cli"
    finally:
        util.enable_color()
