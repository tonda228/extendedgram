import json
import os

# project_path = os.path.abspath(".")
# with open(os.path.abspath(os.path.join(project_path, "config.json"))) as f:
#     config_file = json.load(f)

with open("config.json") as f:
    config_file = json.load(f)