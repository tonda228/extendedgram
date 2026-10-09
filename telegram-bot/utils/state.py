from utils.classes import AppUser, AppUserPreloading, UninitializedUser

user_info: dict[int, AppUser] = {}
user_preloading: dict[int, AppUserPreloading] = {}
uninitialized_users: dict[int, UninitializedUser] = {}