from heapq import heapify, heappush
import os

from starlette.websockets import WebSocketDisconnect

from bot import application
from database.groups import get_app_user_groups
from utils.classes import Host, Request, UserGroupDBObject
from utils.helpers import send_message

available_hosts: dict[int, Host] = dict()
available_group_hosts: dict[int, list[Host]] = dict()
active_requests: dict[int, Request] = dict()

async def close_websocket(host: Host, notify: bool=True, code: int = 1000, reason: str = ""):
    try:
        await host.websocket.close(code=code, reason=reason)
    except (WebSocketDisconnect, OSError, RuntimeError):
        pass
    if notify:
        await send_message(host.user.user_id, "Your machine stopped responding. Please check that everything "
                                          "is functioning again and reconnect.")

async def finish_requests(host: Host):
    for request_id, request in list(active_requests.items()):
        if request.host is host:
            active_requests.pop(request_id, None)
            if not request.future.done():
                request.future.set_result(None)

async def disconnect_host(host: Host):
    host.disconnected = True
    if available_hosts.get(host.user.user_id) is host:
        del available_hosts[host.user.user_id]
        from utils.session import record_activity
        record_activity(host.user.user_id)
    for hosts in available_group_hosts.values():
        hosts[:] = [stored_host for stored_host in hosts if stored_host is not host]
        heapify(hosts)

def update_group(group: UserGroupDBObject, host: Host, add_back=False):
    hosts = available_group_hosts.get(group.group_id)
    if hosts is None:
        return
    for index, stored_host in enumerate(hosts):
        if stored_host is host:
            hosts.pop(index)
            heapify(hosts)
            if add_back:
                heappush(hosts, host)
            break

def update_host_priorities(host: Host):
    for hosts in available_group_hosts.values():
        if any(stored_host is host for stored_host in hosts):
            heapify(hosts)

def select_host(user_id: int, embedding_model: str | None = None) -> tuple[Host, str] | None:
    own_host = available_hosts.get(user_id)
    if own_host is not None and own_host.embedding_models:
        if embedding_model is None:
            return own_host, own_host.embedding_models[0]
        if embedding_model in own_host.embedding_models:
            return own_host, embedding_model

    best = None
    default_model = os.getenv("EMBEDDING_MODEL")
    for group in get_app_user_groups(user_id):
        required_model = group.embedding_model or default_model
        if not required_model or (embedding_model is not None and embedding_model != required_model):
            continue
        heap = available_group_hosts.get(group.group_id)
        if not heap:
            continue
        host = heap[0] if required_model in heap[0].embedding_models else min(
            (host for host in heap if required_model in host.embedding_models),
            key=lambda host: host.requests_count, default=None)
        if host is not None and (best is None or host.requests_count < best[0].requests_count):
            best = (host, required_model)
    return best


async def get_host(user_id: int, embedding_model: str | None = None) -> Host | None:
    selection = select_host(user_id, embedding_model)
    return selection[0] if selection else None
