from bot import application
from utils.classes import Host, Request
from utils.state import user_info

available_hosts: dict[int, Host] = dict()
active_requests: dict[int, Request] = dict()

async def get_host(user_id):
    app_user = user_info[user_id]
    if user_id in available_hosts:
        return available_hosts[user_id]
    else:
        if not app_user.group_id:
            await application.send_message(chat_id=user_id, text="No host is available at the moment.")
            return None

        for available_host in available_hosts.values():
            if available_host.user.group_id == app_user.group_id:
                return available_host
    return None