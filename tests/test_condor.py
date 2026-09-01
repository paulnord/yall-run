from yawl_run.backend import render_condor
from yawl_run.model import load_spec


def test_condor_dag_render(tmp_path):
    spec_file = tmp_path / "campaign.toml"
    spec_file.write_text(
        """[campaign]\nname = \"dag-test\"\nbackend = \"condor\"\n\n"
        "[[task]]\nname = \"left\"\ncommand = \"echo left\"\nretries = 2\n\n"
        "[[task]]\nname = \"right\"\ncommand = \"echo right\"\n\n"
        "[[task]]\nname = \"finish\"\ncommand = \"echo finish\"\nparents = [\"left\", \"right\"]\n"
        """
    )
    spec = load_spec(spec_file)
    campaign_dir = render_condor(spec, tmp_path / "campaigns")
    dag = (campaign_dir / "condor" / "campaign.dag").read_text()
    assert "RETRY yawl_0000_left 2" in dag
    assert "PARENT yawl_0000_left yawl_0001_right CHILD yawl_0002_finish" in dag
    assert (campaign_dir / "condor" / "yawl_worker.py").is_file()
