import pytest

from src import login_item


@pytest.fixture
def tmp_plist(tmp_path, monkeypatch):
    """Redirect the plist path to a temp directory."""
    plist = tmp_path / "com.user.diskspacewidget.plist"
    monkeypatch.setattr(login_item, "PLIST_PATH", plist)
    return plist


def test_is_enabled_false_when_plist_missing(tmp_plist):
    assert login_item.is_enabled() is False


def test_is_enabled_true_when_plist_present(tmp_plist):
    tmp_plist.write_text("<?xml version='1.0'?><plist/>")
    assert login_item.is_enabled() is True


from unittest.mock import patch


def test_enable_writes_plist_and_loads(tmp_plist):
    with patch("src.login_item.subprocess.run") as mock_run:
        login_item.enable()

    assert tmp_plist.exists()
    content = tmp_plist.read_text()
    assert "<key>Label</key>" in content
    assert f"<string>{login_item.PLIST_LABEL}</string>" in content
    assert "<key>ProgramArguments</key>" in content
    assert "<key>RunAtLoad</key>" in content
    assert "<true/>" in content

    # Should have called launchctl unload (ignored errors) then load.
    assert mock_run.call_count == 2
    unload_call, load_call = mock_run.call_args_list
    assert unload_call.args[0][:2] == ["launchctl", "unload"]
    assert load_call.args[0][:2] == ["launchctl", "load"]


def test_enable_is_idempotent_when_already_loaded(tmp_plist):
    # unload raises (plist loaded or not) — enable should swallow it and still load
    def run_side_effect(args, **kwargs):
        if args[:2] == ["launchctl", "unload"]:
            raise FileNotFoundError("not loaded")
        return None

    with patch("src.login_item.subprocess.run", side_effect=run_side_effect) as mock_run:
        login_item.enable()

    assert tmp_plist.exists()
    assert mock_run.call_count == 2


def test_disable_removes_plist_and_unloads(tmp_plist):
    tmp_plist.write_text("<?xml version='1.0'?><plist/>")
    with patch("src.login_item.subprocess.run") as mock_run:
        login_item.disable()

    assert not tmp_plist.exists()
    assert mock_run.call_count == 1
    assert mock_run.call_args.args[0][:2] == ["launchctl", "unload"]


def test_disable_is_safe_when_plist_missing(tmp_plist):
    # No plist exists. disable() should not raise.
    with patch("src.login_item.subprocess.run") as mock_run:
        login_item.disable()
    assert not tmp_plist.exists()
    # Nothing to unload, so subprocess.run shouldn't be called.
    assert mock_run.call_count == 0
