"""Node-prerequisite deployment: unit activation and the sentinel drop-in.

These cover three bugs found by deploying to the real nodes, each of which the
CLI reported as a successful install:
  * an unquoted Environment= that systemd silently truncated,
  * `enable --now` leaving a corrected script on disk but not running,
  * and a boot-only rotation unit that must never be restarted mid-run.
"""

from homelab.runner import RecordingRunner
from homelab.steps import prereqs


def test_probe_ips_dropin_is_quoted():
    """Unquoted, systemd splits Environment= on the space and drops every peer
    after the first. The sentinel would then probe one peer, and losing that
    single peer would look like isolation -- rebooting a healthy node."""
    runner = RecordingRunner()
    prereqs.enable_node_units(runner, probe_ips="10.0.0.1 10.0.0.2")
    written = "\n".join(runner.commands)
    assert 'Environment="PROBE_IPS=10.0.0.1 10.0.0.2"' in written


def test_running_units_are_restarted_so_updates_take_effect():
    """`enable --now` no-ops on an already-running unit, so an updated script
    sits inert until the next reboot while `install` reports success."""
    runner = RecordingRunner()
    prereqs.enable_node_units(runner, probe_ips="10.0.0.1")
    cmds = runner.commands
    assert "sudo -n systemctl restart netsnap-sentinel.service" in cmds
    assert "sudo -n systemctl restart netsnap-archive.timer" in cmds


def test_preboot_is_enabled_but_never_restarted():
    """Restarting preboot rotates real pre-outage evidence out of the two-deep
    history, replacing it with a snapshot taken from a healthy node."""
    runner = RecordingRunner()
    prereqs.enable_node_units(runner)
    cmds = runner.commands
    assert "sudo -n systemctl enable --now netsnap-preboot.service" in cmds
    assert "sudo -n systemctl restart netsnap-preboot.service" not in cmds


def test_superseded_units_are_stopped_and_removed():
    """The old once-a-minute rotation must not keep running alongside the
    sentinel -- it was what overwrote the only snapshot that ever mattered."""
    runner = RecordingRunner()
    prereqs.remove_superseded(runner)
    cmds = "\n".join(runner.commands)
    assert "netsnap.timer" in cmds
    assert "eee-off@eth0.service" in cmds
    assert "/usr/local/bin/netsnap-rotate.sh" in cmds
