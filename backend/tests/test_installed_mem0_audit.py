from inspect_mem0 import inspect_installed


def test_installed_mem0_behavior_is_explicitly_audited():
    report = inspect_installed()
    assert report["mem0ai_version"] == "2.2.0"
    assert report["mongodb_filter_is_post_vector_search"] is True
    assert report["update_accepts_user_scope"] is False
    assert report["delete_accepts_user_scope"] is False
    assert report["history_accepts_user_scope"] is False
    assert report["history_uses_local_history_db"] is True
