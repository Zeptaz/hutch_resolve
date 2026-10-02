from uuid import UUID

from scripts.seed_run import RUN, render


def test_render_preserves_run_id_and_deterministically_rekeys_fixture_ids():
    run_id = UUID("11111111-1111-4111-8111-111111111111")
    source = "run=" + str(RUN) + "; customer=10000000-0000-0000-0000-000000000001"

    first = render(run_id, source)
    second = render(run_id, source)

    assert first == second
    assert "run=" + str(run_id) in first
    assert "customer=10000000-0000-0000-0000-000000000001" not in first


def test_separate_fixture_runs_have_disjoint_child_ids():
    source = "run=" + str(RUN) + "; account=20000000-0000-0000-0000-000000000001"
    first = render(UUID("11111111-1111-4111-8111-111111111111"), source)
    second = render(UUID("22222222-2222-4222-8222-222222222222"), source)

    assert first.split("account=")[1] != second.split("account=")[1]

