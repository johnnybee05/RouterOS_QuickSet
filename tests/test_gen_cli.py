from factories import router_profile
from mqs.generator.cli import main


def test_default_router_to_stdout(capsys):
    assert main(["--target", "6.49.10", "--ipv6-package", "enabled", "--issues"]) == 0
    out, err = capsys.readouterr()
    assert "primary-ntp=195.113.144.201" in out
    assert "/system package\ndisable ipv6" in out
    assert "info" in err


def test_profile_file_to_output(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(router_profile().model_dump_json(), encoding="utf-8")
    out_path = tmp_path / "out.rsc"
    assert main([str(profile_path), "--target", "7.15.3", "-o", str(out_path)]) == 0
    text = out_path.read_text(encoding="utf-8")
    assert "/system ntp client servers" in text
    assert "\r\n" not in out_path.read_bytes().decode("utf-8")


def test_errors_give_nonzero_exit(capsys):
    assert main(["--ports", ""]) == 1  # Router preset without ports has no WAN interface
    capsys.readouterr()
