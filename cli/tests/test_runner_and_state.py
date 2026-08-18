from __future__ import annotations

import pytest

from homelab import state as state_mod
from homelab.errors import RemoteError, Unreachable
from homelab.runner import RecordingRunner, Result, SSHRunner


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def test_ssh_never_prompts_for_a_password(cluster):
    """A password prompt inside an automation run does not fail — it hangs, which
    is worse. BatchMode turns that into an immediate error."""
    runner = SSHRunner(node=cluster.server, ssh=cluster.spec.ssh, dry_run=True)
    argv = runner._argv(["true"])
    assert "BatchMode=yes" in argv


def test_ssh_reuses_connections(cluster):
    runner = SSHRunner(node=cluster.server, ssh=cluster.spec.ssh, dry_run=True)
    argv = runner._argv(["true"])
    assert "ControlMaster=auto" in argv
    assert any("ControlPath=" in a for a in argv)


def test_ssh_targets_the_ip_not_the_name(cluster):
    """There is no DNS in this lab; a hostname would resolve only by luck."""
    runner = SSHRunner(node=cluster.server, ssh=cluster.spec.ssh, dry_run=True)
    assert f"{cluster.spec.ssh.user}@192.168.11.7" in runner._argv(["true"])


def test_sudo_is_non_interactive():
    runner = RecordingRunner()
    runner.run(["whoami"], sudo=True)
    assert runner.log[0][:2] == ["sudo", "-n"]


def test_failed_command_raises_with_context():
    runner = RecordingRunner(responses={"boom": Result(3, "", "it exploded")})
    with pytest.raises(RemoteError) as exc:
        runner.run(["boom"])
    assert exc.value.returncode == 3
    assert "it exploded" in str(exc.value)


def test_check_false_returns_instead_of_raising():
    runner = RecordingRunner(responses={"boom": Result(3, "", "no")})
    assert runner.run(["boom"], check=False).returncode == 3


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def test_token_is_never_persisted(tmp_path):
    """The join token is a cluster-admin credential and this repo is public. Only
    a fingerprint is stored — enough to notice the server was rebuilt."""
    token = "K10secret::server:supersecretvalue"
    state = state_mod.State(cluster="rps", tokenFingerprint=state_mod.fingerprint(token))
    path = state_mod.save(tmp_path, state)
    written = path.read_text()
    assert "supersecretvalue" not in written
    assert token not in written
    assert "sha256:" in written


def test_fingerprint_changes_when_the_server_is_rebuilt():
    assert state_mod.fingerprint("a") != state_mod.fingerprint("b")
    assert state_mod.fingerprint("a") == state_mod.fingerprint("a")


def test_state_round_trips(tmp_path):
    state = state_mod.State(cluster="rps", k3sVersion="v1.36.3+k3s1")
    state.mark("k3s-server", phase="installed", ip="192.168.11.7", role="server")
    state.mark("k3s-worker-2", phase="pending", role="agent", note="unreachable")
    state_mod.save(tmp_path, state)

    reloaded = state_mod.load(tmp_path, "rps")
    assert reloaded.k3sVersion == "v1.36.3+k3s1"
    assert reloaded.phase("k3s-server") == "installed"
    assert reloaded.pending == ["k3s-worker-2"]


def test_absent_state_file_is_not_an_error(tmp_path):
    state = state_mod.load(tmp_path, "never-built")
    assert state.cluster == "never-built"
    assert state.nodes == {}


def test_save_is_atomic_and_keeps_a_backup(tmp_path):
    first = state_mod.State(cluster="rps", k3sVersion="v1")
    state_mod.save(tmp_path, first)
    second = state_mod.State(cluster="rps", k3sVersion="v2")
    path = state_mod.save(tmp_path, second)

    assert not path.with_suffix(".tmp").exists()
    assert path.with_suffix(".bak").is_file()
    assert state_mod.load(tmp_path, "rps").k3sVersion == "v2"


def test_pending_nodes_are_tracked_for_later_joining(tmp_path):
    """k3s-worker-2 is added after the clean install by design, so 'not here yet'
    has to be a first-class state rather than a failure."""
    state = state_mod.State(cluster="rps")
    state.mark("k3s-worker-1", phase="joined", role="agent")
    state.mark("k3s-worker-2", phase="pending", role="agent")
    assert state.pending == ["k3s-worker-2"]
    state.mark("k3s-worker-2", phase="joined", role="agent")
    assert state.pending == []


# --------------------------------------------------------------------------
# ssh failure classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "stderr",
    [
        "rudra@192.168.11.5: Permission denied (publickey,password).",
        "Received disconnect: Too many authentication failures",
        "Host key verification failed.",
    ],
)
def test_key_rejection_is_not_treated_as_absent(cluster, monkeypatch, stderr):
    """A host that answers and refuses the key must not be filed as 'not built
    yet'. Waiting cannot fix it, and conflating the two would silently skip a
    misconfigured node forever."""
    from homelab.errors import AuthError

    _fake_ssh(monkeypatch, returncode=255, stderr=stderr)
    runner = SSHRunner(node=cluster.server, ssh=cluster.spec.ssh)
    with pytest.raises(AuthError):
        runner.run(["true"])


@pytest.mark.parametrize(
    "stderr",
    [
        "ssh: connect to host 192.168.11.5 port 22: No route to host",
        "ssh: connect to host 192.168.11.5 port 22: Connection timed out",
        "ssh: connect to host 192.168.11.5 port 22: Connection refused",
    ],
)
def test_no_answer_is_pending_not_fatal(cluster, monkeypatch, stderr):
    """Hardware that does not exist yet is an expected state: k3s-worker-2 joins
    after the clean install by design."""
    _fake_ssh(monkeypatch, returncode=255, stderr=stderr)
    runner = SSHRunner(node=cluster.server, ssh=cluster.spec.ssh)
    with pytest.raises(Unreachable):
        runner.run(["true"])


def test_known_hosts_noise_is_stripped_from_the_reason(cluster, monkeypatch):
    from homelab.errors import AuthError

    _fake_ssh(
        monkeypatch,
        returncode=255,
        stderr="Warning: Permanently added '192.168.11.5' to the list of known hosts.\n"
        "rudra@192.168.11.5: Permission denied (publickey).",
    )
    runner = SSHRunner(node=cluster.server, ssh=cluster.spec.ssh)
    with pytest.raises(AuthError) as exc:
        runner.run(["true"])
    assert "known hosts" not in exc.value.detail
    assert "Permission denied" in exc.value.detail


def _fake_ssh(monkeypatch, *, returncode: int, stderr: str):
    import subprocess

    class Proc:
        def __init__(self):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = stderr

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())


def test_dry_run_skips_mutations_but_still_reads(cluster, monkeypatch):
    """A dry run that short-circuits read-only probes invents state — it reported
    an unreachable node as 'already joined'. Probes must still execute."""
    calls: list[list[str]] = []

    class Proc:
        returncode, stdout, stderr = 0, "yes", ""

    import subprocess

    def fake(argv, **kwargs):
        calls.append(argv)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake)
    runner = SSHRunner(node=cluster.server, ssh=cluster.spec.ssh, dry_run=True)

    runner.run(["rm", "-rf", "/"], mutates=True)
    assert calls == [], "a mutating command must not run during a dry run"

    result = runner.run(["test", "-x", "/usr/local/bin/k3s"], mutates=False)
    assert len(calls) == 1, "a read-only probe must still run during a dry run"
    assert result.out == "yes"
