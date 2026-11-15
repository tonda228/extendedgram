import json


def test_search_returns_host_answer(load_module, run_async):
    module = load_module("host/search.py")
    module.send_data.return_value = ["Friday"]
    data = [{"type": "text", "text": "The deadline is Friday."}]

    result = run_async(module.process_search_request(data, "When is the deadline?"))

    assert result == "Friday"
    assert module.send_data.call_args.args[0] == data
    assert "When is the deadline?" in module.send_data.call_args.args[1]


def test_search_combines_partial_answers(load_module, run_async):
    module = load_module("host/search.py")
    module.send_data.side_effect = [["Friday", "At noon"], ["Friday at noon"]]

    result = run_async(module.process_search_request([], "When?"))

    assert result == "Friday at noon"
    assert module.send_data.await_count == 2
    assert module.send_data.call_args.args[0] == [
        {"type": "text", "text": "Friday"},
        {"type": "text", "text": "At noon"},
    ]


def test_summary_returns_topics(load_module, run_async):
    module = load_module("host/summarize.py")
    topics = [{"start_message_id": 1, "end_message_id": 2, "topic": "Deadline", "summary": "Submit on Friday."}]
    module.send_data.return_value = topics

    assert run_async(module.process_summarize_request([])) == topics
    assert module.send_data.await_count == 1


def test_summary_combines_more_than_ten_topics(load_module, run_async):
    module = load_module("host/summarize.py")
    topics = [{"start_message_id": 1, "end_message_id": 2, "topic": str(index), "summary": "A decision"} for index in range(11)]
    combined = [{"start_message_id": 1, "end_message_id": 2, "topic": "Decisions", "summary": "Combined summary"}]
    module.send_data.side_effect = [topics, combined]

    assert run_async(module.process_summarize_request([])) == combined
    assert module.send_data.await_count == 2
    sent_data = module.send_data.call_args.args[0]
    assert len(sent_data) == 11
    assert json.loads(sent_data[0]["text"]) == topics[0]
