from pathlib import Path

from caddy_tui import helper_runner


class DummyCommand(helper_runner.HelperCommand):
    def __init__(self):
        super().__init__(["sudo", "caddy-tui-helper", "noop"])


def test_stage_caddyfile_copy_builds_command(monkeypatch, tmp_path):
    source = tmp_path / "Caddyfile"
    source.write_text("test")

    monkeypatch.setattr(helper_runner, "_build_base_command", lambda **_kwargs: ["sudo", "helper"])
    monkeypatch.setattr(helper_runner, "_run_helper", lambda args: DummyCommand())
    staged, cmd, error = helper_runner.stage_caddyfile_copy(source)
    assert staged is not None
    assert cmd.endswith("noop")
    assert error is None


def test_install_generated_file_handles_failure(monkeypatch, tmp_path):
    source = tmp_path / "generated"
    source.write_text("data")

    def fail(args):
        raise helper_runner.HelperInvocationError(DummyCommand(), "denied")

    monkeypatch.setattr(helper_runner, "_build_base_command", lambda **_kwargs: ["sudo", "helper"])
    monkeypatch.setattr(helper_runner, "_run_helper", fail)
    success, cmd, error = helper_runner.install_generated_file(source, Path("/etc/caddy/Caddyfile"))
    assert not success
    assert cmd is not None
    assert error == "denied"


def test_check_caddy_service_success(monkeypatch):
    def fake_base_command(**_kwargs):
        return ["sudo", "helper"]

    def fake_run(args, capture_output=False):
        assert capture_output is True
        return DummyCommand(), "active"

    monkeypatch.setattr(helper_runner, "_build_base_command", fake_base_command)
    monkeypatch.setattr(helper_runner, "_run_helper", fake_run)
    state, cmd, error = helper_runner.check_caddy_service()
    assert state == "active"
    assert cmd is not None
    assert error is None


def test_check_caddy_service_failure(monkeypatch):
    def fake_base_command(**_kwargs):
        return ["sudo", "helper"]

    def fail(*args, **_kwargs):
        raise helper_runner.HelperInvocationError(DummyCommand(), "boom")

    monkeypatch.setattr(helper_runner, "_build_base_command", fake_base_command)
    monkeypatch.setattr(helper_runner, "_run_helper", fail)
    state, cmd, error = helper_runner.check_caddy_service()
    assert state is None
    assert cmd is not None
    assert error == "boom"


def test_restart_caddy_service(monkeypatch):
    calls: list[list[str]] = []

    def fake_base_command(**_kwargs):
        return ["sudo", "helper"]

    def fake_run(args, capture_output=False):
        calls.append(args)
        return DummyCommand()

    monkeypatch.setattr(helper_runner, "_build_base_command", fake_base_command)
    monkeypatch.setattr(helper_runner, "_run_helper", fake_run)
    success, cmd, error = helper_runner.restart_caddy_service()
    assert success is True
    assert cmd is not None
    assert error is None
    assert any(arg_list[-1] == "restart" for arg_list in calls)
