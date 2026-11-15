from types import SimpleNamespace


def make_group(group_id, embedding_model=None):
    return SimpleNamespace(group_id=group_id, embedding_model=embedding_model)


def make_host(requests_count, *embedding_models):
    return SimpleNamespace(requests_count=requests_count, embedding_models=embedding_models or ("test-embedding",))


def test_user_prefers_own_host(load_module, run_async, monkeypatch):
    module = load_module("server/__init__.py")
    host = make_host(0, "model-a", "model-b")
    module.available_hosts[123] = host

    assert run_async(module.get_host(123)) is host
    assert module.select_host(123) == (host, "model-a")


def test_user_can_use_host_from_same_group(load_module, run_async, monkeypatch):
    module = load_module("server/__init__.py")
    module.get_app_user_groups.return_value = [make_group(1)]
    other_group = make_host(0)
    same_group = make_host(2)
    module.available_group_hosts.update({2: [other_group], 1: [same_group]})

    assert run_async(module.get_host(123)) is same_group


def test_host_from_another_group_is_not_used(load_module, run_async, monkeypatch):
    module = load_module("server/__init__.py")
    module.get_app_user_groups.return_value = [make_group(1)]
    module.available_group_hosts[2] = [SimpleNamespace(requests_count=0)]

    assert run_async(module.get_host(123)) is None


def test_host_lookup_does_not_send_duplicate_notifications(load_module, run_async, monkeypatch):
    module = load_module("server/__init__.py")

    assert run_async(module.get_host(123)) is None
    module.application.bot.send_message.assert_not_awaited()


def test_user_selects_least_loaded_host_across_groups(load_module, run_async):
    module = load_module("server/__init__.py")
    module.get_app_user_groups.return_value = [make_group(1), make_group(2)]
    busy = make_host(4)
    idle = make_host(0)
    module.available_group_hosts.update({1: [busy], 2: [idle]})

    assert run_async(module.get_host(123)) is idle


def test_empty_group_heap_is_skipped(load_module, run_async):
    module = load_module("server/__init__.py")
    module.get_app_user_groups.return_value = [make_group(1)]
    module.available_group_hosts[1] = []

    assert run_async(module.get_host(123)) is None


def test_group_model_ignores_incompatible_host_even_when_less_busy(load_module, run_async):
    module = load_module("server/__init__.py")
    group = make_group(1, "model-b")
    module.get_app_user_groups.return_value = [group]
    model_a = make_host(0, "model-a")
    model_b = make_host(2, "model-b")
    module.available_group_hosts[1] = [model_a, model_b]

    assert run_async(module.get_host(123)) is model_b
    assert run_async(module.get_host(123, embedding_model="model-a")) is None
    group.embedding_model = "model-a"
    assert run_async(module.get_host(123)) is model_a


def test_host_retry_cannot_switch_search_embedding_models(load_module, run_async):
    module = load_module("server/__init__.py")
    module.get_app_user_groups.return_value = [make_group(1, "model-a"), make_group(2, "model-b")]
    model_a = make_host(2, "model-a")
    model_b = make_host(0, "model-b")
    module.available_group_hosts.update({1: [model_a], 2: [model_b]})

    assert run_async(module.get_host(123)) is model_b
    assert run_async(module.get_host(123, embedding_model="model-a")) is model_a


def test_one_host_can_serve_groups_with_different_models(load_module, run_async):
    module = load_module("server/__init__.py")
    group_a = make_group(1, "model-a")
    group_b = make_group(2, "model-b")
    host = make_host(0, "model-a", "model-b")
    module.available_group_hosts.update({1: [host], 2: [host]})
    module.get_app_user_groups.side_effect = lambda user_id: [group_a if user_id == 123 else group_b]

    assert module.select_host(123) == (host, "model-a")
    assert module.select_host(456) == (host, "model-b")
    assert run_async(module.get_host(456, embedding_model="model-a")) is None
