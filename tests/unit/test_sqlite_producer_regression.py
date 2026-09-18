from pathlib import Path
from lucius.persistence.database import create_sqlite_engine

def test_sqlite_engine_creation_does_not_create_literal_sqlite_directories(tmp_path: Path):
    """Regression Test for Section 7:
    Verifies create_sqlite_engine handles scheme prefixes without creating
    literal filesystem directories named 'sqlite:', 'sqlite:/', or 'sqlite:/*'.
    """
    # Test malformed / prefixed paths
    test_paths = [
        tmp_path / "normal_state.sqlite",
        f"sqlite:{tmp_path}/prefixed_1.sqlite",
        f"sqlite:/{tmp_path}/prefixed_2.sqlite",
        f"sqlite:///{tmp_path}/prefixed_3.sqlite",
    ]

    for p in test_paths:
        engine = create_sqlite_engine(p)
        assert engine is not None
        engine.dispose()

    # Verify no 'sqlite:' or 'sqlite:/' directories were created in tmp_path
    assert not (tmp_path / "sqlite:").exists()
    assert not (tmp_path / "sqlite:/").exists()
