from ryuo.cli import main


def test_cli_dry_run_is_default_and_prints_hex(capsys):
    assert main(["config", "--media", "clip.mp4", "--slots", "CPU Temperature", "Fan Speed Claude"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "# config seq=1" in out
    assert out.strip().splitlines()[-1].endswith("5a")


def test_cli_oversize_reports_error(capsys):
    names = [f"clip_with_a_quite_long_name_{i:03d}.mp4" for i in range(40)]
    assert main(["config", "--play-mode", "Cycle", "--media", *names]) == 2
    assert "limit is 1024" in capsys.readouterr().err


def test_cli_profiles(capsys):
    assert main(["profiles"]) == 0
    assert "1.0.10" in capsys.readouterr().out
